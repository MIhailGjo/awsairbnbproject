# customerdata: log in and create accounts using the Aurora "users" table.
# The web page POSTs JSON like {"action": "login", "email": "..."} and gets
# the user's info back: user_id, name, email, home_city, member_since,
# plus "airbnbs": the Airbnbs they added.
# "add_airbnb" reserves a searched Airbnb for the user (Aurora user_airbnbs table)
# and marks the property in DynamoDB as reserved; only one user can have it.
# "remove_airbnb" unreserves it again.

import base64
import json
import os
import ssl
from datetime import date, datetime, timezone

import boto3
from botocore.exceptions import ClientError
import pg8000.native  # small pure-Python PostgreSQL driver (bundled in the zip)

# Aurora connection settings (set in the Lambda's environment variables)
DB_HOST = os.environ["DB_HOST"]  # e.g. airbnb-users.cluster-....rds.amazonaws.com
DB_USER = os.environ.get("DB_USER", "postgres")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_PORT = 5432

# The columns we send back to the page
USER_COLUMNS = "user_id, name, email, home_city, member_since"
AIRBNB_COLUMNS = "address, formatted_address, price_per_night, beds, baths, added_at"

# DynamoDB table with the searched properties (same one the search Lambda uses)
properties = boto3.resource("dynamodb").Table(os.environ.get("PROPERTIES_TABLE", "airbnb-properties"))

rds = boto3.client("rds")
_conn = None  # reused between calls while the Lambda stays warm


def db():
    """Connect to Aurora with an IAM token instead of a password."""
    global _conn
    if _conn is None:
        token = rds.generate_db_auth_token(DBHostname=DB_HOST, Port=DB_PORT, DBUsername=DB_USER)
        _conn = pg8000.native.Connection(
            user=DB_USER, password=token, host=DB_HOST, port=DB_PORT, database=DB_NAME,
            ssl_context=ssl.create_default_context(),
            timeout=25,  # Aurora can take ~15s to wake up after pausing
        )
    return _conn


def query(sql, **params):
    """Run SQL and return the rows as dicts; reconnects once if the old connection died."""
    global _conn
    for attempt in (1, 2):
        try:
            conn = db()
            rows = conn.run(sql, **params)
            cols = [c["name"] for c in conn.columns or []]
            return [dict(zip(cols, r)) for r in rows]
        except (pg8000.exceptions.InterfaceError, OSError):
            _conn = None  # connection went stale, try a fresh one
            if attempt == 2:
                raise


# DynamoDB is capped (provisioned capacity) to stay in the free tier;
# when it's over the cap it sends back one of these instead of charging more
THROTTLED = ("ProvisionedThroughputExceededException", "ThrottlingException")


def lambda_handler(event, context):
    """Runs every request; if DynamoDB is over its cap, say "busy" instead of crashing."""
    try:
        return handle(event)
    except ClientError as err:
        if err.response["Error"]["Code"] in THROTTLED:
            return response(503, {"error": "Too busy right now, try again in a moment"})
        raise


def handle(event):
    try:
        body = parse_body(event)
    except ValueError:
        return response(400, {"error": "Request body must be valid JSON"})

    # Route on the URL path (/signup, /login) or an "action" field in the body
    path = (event.get("rawPath") or event.get("path") or "").rstrip("/").lower()
    action = (body.get("action") or path.rsplit("/", 1)[-1]).lower()

    if action in ("signup", "register"):
        return signup(body)
    if action == "login":
        return login(body)
    if action == "add_airbnb":
        return add_airbnb(body)
    if action == "remove_airbnb":
        return remove_airbnb(body)
    return response(400, {"error": 'Unknown action. Use "signup", "login", "add_airbnb" or "remove_airbnb".'})


def user_airbnbs(user_id):
    """All Airbnbs a user has added, newest first."""
    return query(
        f"SELECT {AIRBNB_COLUMNS} FROM user_airbnbs WHERE user_id = :id ORDER BY added_at DESC",
        id=user_id,
    )


def login(body):
    """Look up a user by email and return their info and their Airbnbs."""
    email = (body.get("email") or "").strip().lower()
    if not email:
        return response(400, {"error": "email is required"})

    rows = query(f"SELECT {USER_COLUMNS} FROM users WHERE lower(email) = :email LIMIT 1", email=email)
    if not rows:
        return response(404, {"error": "No account with that email"})
    user = rows[0]
    user["airbnbs"] = user_airbnbs(user["user_id"])
    return response(200, user)


def add_airbnb(body):
    """Save a searched Airbnb to a user, in Aurora and in DynamoDB.

    body = {"user_id": 1, "address": "123 main st"}  (address = the DynamoDB key)
    Returns the user's updated list of Airbnbs.
    """
    user_id = body.get("user_id")
    address = (body.get("address") or "").strip().lower()
    if not isinstance(user_id, int) or not address:
        return response(400, {"error": "user_id and address are required"})

    # 1. the user must exist
    users = query("SELECT email FROM users WHERE user_id = :id", id=user_id)
    if not users:
        return response(404, {"error": "No such user"})
    email = users[0]["email"]

    # 2. get the property's details from DynamoDB (not from the page, so they can't be faked)
    prop = properties.get_item(Key={"address": address}).get("Item")
    if not prop:
        return response(404, {"error": "That Airbnb isn't in the database"})

    # 3. mark it reserved in DynamoDB, but only if nobody else has it
    #    (the check and the write happen together, so two people can't both get it)
    try:
        properties.update_item(
            Key={"address": address},
            UpdateExpression="ADD addedBy :user SET lastAddedAt = :now",
            ConditionExpression="attribute_not_exists(addedBy) OR contains(addedBy, :email)",
            ExpressionAttributeValues={
                ":user": {email},  # addedBy is a set of emails
                ":email": email,
                ":now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
    except properties.meta.client.exceptions.ConditionalCheckFailedException:
        return response(409, {"error": "Already reserved"})

    # 4. save it to the user in Aurora (skipped if they already have it)
    added = query(
        "INSERT INTO user_airbnbs (user_id, address, formatted_address, price_per_night, beds, baths) "
        "VALUES (:user_id, :address, :formatted, :price, :beds, :baths) "
        "ON CONFLICT (user_id, address) DO NOTHING RETURNING id",
        user_id=user_id, address=address, formatted=prop.get("formattedAddress"),
        price=int(prop.get("pricePerNight", 0)), beds=int(prop.get("beds", 0)),
        baths=int(prop.get("baths", 0)),
    )
    if not added:
        return response(409, {"error": "You already reserved this Airbnb"})

    return response(200, {"airbnbs": user_airbnbs(user_id)})


def remove_airbnb(body):
    """Unreserve: take an Airbnb off a user, in Aurora and in DynamoDB.

    body = {"user_id": 1, "address": "123 main st"}
    Returns the user's updated list of Airbnbs.
    """
    user_id = body.get("user_id")
    address = (body.get("address") or "").strip().lower()
    if not isinstance(user_id, int) or not address:
        return response(400, {"error": "user_id and address are required"})

    users = query("SELECT email FROM users WHERE user_id = :id", id=user_id)
    if not users:
        return response(404, {"error": "No such user"})

    # 1. remove it from the user in Aurora
    removed = query(
        "DELETE FROM user_airbnbs WHERE user_id = :user_id AND address = :address RETURNING id",
        user_id=user_id, address=address,
    )
    if not removed:
        return response(404, {"error": "You haven't reserved this Airbnb"})

    # 2. free it up in DynamoDB (removing the last email deletes addedBy)
    properties.update_item(
        Key={"address": address},
        UpdateExpression="DELETE addedBy :user",
        ExpressionAttributeValues={":user": {users[0]["email"]}},
    )

    return response(200, {"airbnbs": user_airbnbs(user_id)})


def signup(body):
    """Create a user (name, email, home city) and return their info."""
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    home_city = (body.get("home_city") or "").strip() or None
    if not name or not email:
        return response(400, {"error": "name and email are required"})

    # emails must be unique
    if query("SELECT 1 FROM users WHERE lower(email) = :email", email=email):
        return response(409, {"error": "An account with that email already exists"})

    rows = query(
        f"INSERT INTO users (name, email, home_city, member_since) "
        f"VALUES (:name, :email, :home_city, CURRENT_DATE) RETURNING {USER_COLUMNS}",
        name=name, email=email, home_city=home_city,
    )
    return response(201, rows[0])


def parse_body(event):
    # API Gateway / function URLs send the body as a string; direct invokes send a dict
    body = event.get("body", event)
    if body is None:
        return {}
    if isinstance(body, str):
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode()
        try:
            body = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            raise ValueError("Invalid JSON") from e
    if not isinstance(body, dict):
        raise ValueError("Body must be a JSON object")
    return body


def to_json(obj):
    # dates from the database (member_since, added_at) become "2026-10-21..."
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError


def response(status, payload):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, default=to_json),
    }

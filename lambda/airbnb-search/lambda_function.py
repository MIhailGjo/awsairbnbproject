# airbnb-search: looks up an address and returns rental info for it.
# Checks DynamoDB first; if the address isn't there it geocodes it, makes up
# rental info, and saves it. Every search stamps lastCalled on the item
# and is also written as its own row in the search log table.

# --- Imports: tools this code borrows from Python and AWS ---
import json                              # turn Python data into JSON text and back
import os                                # read settings (environment variables)
import random                            # make up random numbers
import urllib.parse                      # build safe web addresses (URLs)
import urllib.request                    # call other websites (Google Maps)
import uuid                              # make unique ids for log rows
from datetime import datetime, timezone  # get the current date and time
from decimal import Decimal              # number type DynamoDB requires

import boto3                             # AWS library for talking to DynamoDB
from botocore.exceptions import ClientError  # errors AWS sends back

# --- Setup: runs once when the Lambda starts ---
# The DynamoDB table where addresses are saved (its name is set in the Lambda settings)
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
# Second table: one row for every search (name set in SEARCH_LOG_TABLE)
# If SEARCH_LOG_TABLE isn't set, searches still work, they just aren't logged
LOG_TABLE_NAME = os.environ.get("SEARCH_LOG_TABLE")
log_table = boto3.resource("dynamodb").Table(LOG_TABLE_NAME) if LOG_TABLE_NAME else None
# Google Maps key; if it's missing we use fake_geocode instead
MAPS_KEY = os.environ.get("GOOGLE_API_KEY")  # not set yet -> fake_geocode is used


def to_json(obj):
    """Helper: convert DynamoDB numbers (Decimal) into normal numbers for JSON."""
    # dynamo gives back Decimals and json.dumps chokes on them
    if isinstance(obj, Decimal):
        # whole numbers become int (3), others become float (3.5)
        return float(obj) if obj % 1 else int(obj)
    raise TypeError


def respond(code, body):
    """Helper: build the reply that gets sent back to the web page.

    code = HTTP status (200 ok, 400 bad request, 404 not found)
    body = the data to send, turned into JSON text
    """
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=to_json),
    }


def geocode(addr):
    """Ask Google Maps for the real location of an address.

    Returns Google's best match, or None if it couldn't find it.
    """
    # build the Google Maps URL with the address and our key
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urllib.parse.urlencode(
        {"address": addr, "key": MAPS_KEY}
    )
    # call Google (give up after 5 seconds) and read the JSON answer
    with urllib.request.urlopen(url, timeout=5) as r:
        data = json.loads(r.read())
    if data.get("status") != "OK":
        return None
    return data["results"][0]  # first (best) result


def fake_geocode(addr):
    """Pretend version of geocode: random coordinates around Washington, DC."""
    # stand-in until the maps key is set up, same shape google returns
    return {
        "formatted_address": addr.title(),  # "123 main st" -> "123 Main St"
        "geometry": {"location": {
            "lat": round(random.uniform(38.8, 39.1), 6),
            "lng": round(random.uniform(-77.3, -76.9), 6),
        }},
    }


def suggest(text, limit=5):
    """Find up to 5 saved addresses that contain the typed text.

    Used for the dropdown while the user is typing. Only reads the database.
    """
    text = text.lower()  # saved keys are lowercase
    found = []
    scan_args = {
        # only keep items whose address contains the typed text
        "FilterExpression": "contains(address, :t)",
        "ExpressionAttributeValues": {":t": text},
        # we only need the nice-looking address back
        "ProjectionExpression": "formattedAddress",
    }
    # DynamoDB reads the table in pages; keep going until we have enough
    while len(found) < limit:
        page = table.scan(**scan_args)
        found += [i["formattedAddress"] for i in page.get("Items", [])]
        if "LastEvaluatedKey" not in page:
            break  # reached the end of the table
        scan_args["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return found[:limit]


def log_search(raw, now, result):
    """Save one row to the search log: what was typed, when, and what happened.

    result = "cached" (was in the database), "new" (just added) or "not_found"
    """
    if log_table is None:
        return  # logging not set up yet
    try:
        log_table.put_item(Item={
            "id": str(uuid.uuid4()),  # unique id so every search gets its own row
            "address": raw,           # exactly what the user typed
            "searchedAt": now,        # when the search happened
            "result": result,
        })
    except Exception as err:
        # a logging problem (missing table, no permission) must not break the search
        print("search log failed:", err)


# DynamoDB is capped (provisioned capacity) to stay in the free tier;
# when it's over the cap it sends back one of these instead of charging more
THROTTLED = ("ProvisionedThroughputExceededException", "ThrottlingException")


def lambda_handler(event, context):
    """Runs every request; if DynamoDB is over its cap, say "busy" instead of crashing."""
    try:
        return handle(event)
    except ClientError as err:
        if err.response["Error"]["Code"] in THROTTLED:
            return respond(503, {"error": "Too busy right now, try again in a moment"})
        raise


def handle(event):
    """Main entry point: AWS runs this every time the URL is called.

    event = info about the request, e.g. ?address=123 Main St
    or ?suggest=main for the typing dropdown
    """
    params = event.get("queryStringParameters") or {}

    # 0. Typing dropdown: ?suggest=... -> return matching saved addresses
    typed = (params.get("suggest") or "").strip()
    if typed:
        return respond(200, {"suggestions": suggest(typed)})

    # 1. Read the address from the URL; stop if none was given
    raw = (params.get("address") or "").strip()
    if not raw:
        return respond(400, {"error": "no address given"})

    # lowercase so "123 Main St" and "123 main st" count as the same address
    key = raw.lower()
    # current time in UTC, e.g. "2026-10-21T14:03:12+00:00"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 2. Already in the database? Return the saved copy
    hit = table.get_item(Key={"address": key}).get("Item")
    if hit:
        # remember when this address was last searched
        table.update_item(
            Key={"address": key},
            UpdateExpression="SET lastCalled = :now",
            ExpressionAttributeValues={":now": now},
        )
        hit["lastCalled"] = now
        hit["cached"] = True  # tells the page this came from the database
        # addedBy = emails of who reserved it; only tell the page yes/no
        hit["reserved"] = bool(hit.pop("addedBy", None))
        log_search(raw, now, "cached")
        return respond(200, hit)

    # 3. Not in the database: look up the location
    #    (real Google Maps if we have a key, otherwise the fake one)
    result = geocode(raw) if MAPS_KEY else fake_geocode(raw)
    if result is None:
        log_search(raw, now, "not_found")
        return respond(404, {"error": "couldn't find that address"})

    # 4. Build the new record to save
    loc = result["geometry"]["location"]
    item = {
        "address": key,                                  # the lookup key
        "formattedAddress": result["formatted_address"], # nicely written address
        "lat": Decimal(str(loc["lat"])),                 # latitude
        "lng": Decimal(str(loc["lng"])),                 # longitude
        # made up rental info
        "pricePerNight": random.randint(80, 450),
        "beds": random.randint(1, 5),
        "baths": random.choice([1, 1, 2, 2, 3]),
        "lastCalled": now,
    }

    # 5. Save it to DynamoDB so the next search finds it
    table.put_item(Item=item)
    log_search(raw, now, "new")

    # 6. Send it back to the page
    item["cached"] = False  # tells the page this is brand new
    item["reserved"] = False
    return respond(200, item)

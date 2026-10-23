# airbnb-search: looks up an address and returns rental info for it.
# Checks DynamoDB first; if the address isn't there it looks it up with a real
# map API (Photon / OpenStreetMap, or Google Maps if a key is set), saves the
# address details plus made-up rental info, and returns it. Every search stamps lastCalled on the item
# and is also written as its own row in the search log table.

# --- Imports: tools this code borrows from Python and AWS ---
import json                              # turn Python data into JSON text and back
import os                                # read settings (environment variables)
import random                            # make up random numbers
import urllib.parse                      # build safe web addresses (URLs)
import urllib.request                    # call other websites (map APIs)
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
# Google Maps key; if it's missing we use Photon (free, no key needed)
MAPS_KEY = os.environ.get("GOOGLE_API_KEY")
# Photon: free address search built on OpenStreetMap data (photon.komoot.io)
PHOTON_URL = "https://photon.komoot.io/api/"
USER_AGENT = "airbnb-class-project/1.0"  # map APIs ask callers to identify themselves
# Prefer places near here (Washington DC / Maryland); farther places still show up, just lower
BIAS_LAT, BIAS_LON = 38.95, -77.0


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


def google_geocode(addr):
    """Ask Google Maps for the real location of an address (only if GOOGLE_API_KEY is set).

    Returns {formatted, lat, lng, street, city, state, postcode, country}, or None.
    """
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urllib.parse.urlencode(
        {"address": addr, "key": MAPS_KEY}
    )
    with urllib.request.urlopen(url, timeout=5) as r:
        data = json.loads(r.read())
    if data.get("status") != "OK":
        return None
    best = data["results"][0]
    # Google splits the address into parts tagged with types like "locality" (city)
    part = {t: c["long_name"] for c in best["address_components"] for t in c["types"]}
    loc = best["geometry"]["location"]
    return {
        "formatted": best["formatted_address"],
        "lat": loc["lat"], "lng": loc["lng"],
        "street": " ".join(filter(None, [part.get("street_number"), part.get("route")])),
        "city": part.get("locality"),
        "state": part.get("administrative_area_level_1"),
        "postcode": part.get("postal_code"),
        "country": part.get("country"),
    }


def photon_search(text, limit, timeout):
    """Ask Photon for real places matching the text.

    Returns a list of {formatted, lat, lng, street, city, state, postcode, country}.
    """
    url = PHOTON_URL + "?" + urllib.parse.urlencode(
        {"q": text, "limit": limit, "lang": "en", "lat": BIAS_LAT, "lon": BIAS_LON}
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())

    places = []
    for f in data.get("features", []):
        p = f["properties"]
        lng, lat = f["geometry"]["coordinates"]  # Photon gives [longitude, latitude]
        # "1600" + "Pennsylvania Avenue" -> "1600 Pennsylvania Avenue"; else the place's name
        street = " ".join(filter(None, [p.get("housenumber"), p.get("street")]))
        first_line = street or p.get("name")
        state_zip = " ".join(filter(None, [p.get("state"), p.get("postcode")]))
        formatted = ", ".join(filter(None, [first_line, p.get("city"), state_zip, p.get("country")]))
        places.append({
            "formatted": formatted,
            "lat": lat, "lng": lng,
            "street": street or None,
            "city": p.get("city"),
            "state": p.get("state"),
            "postcode": p.get("postcode"),
            "country": p.get("country"),
        })
    return places


def geocode(addr):
    """Find the real location of an address: Google if we have a key, otherwise Photon.

    Returns the best match, or None if nothing was found.
    """
    if MAPS_KEY:
        return google_geocode(addr)
    places = photon_search(addr, limit=1, timeout=5)
    return places[0] if places else None


def suggest_saved(text, limit=5):
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

    # 0. Typing dropdown: ?suggest=...
    #    saved  = addresses already in our database (shown in cyan)
    #    places = real addresses from the map API that aren't saved yet (shown in green)
    typed = (params.get("suggest") or "").strip()
    if typed:
        saved = suggest_saved(typed)
        try:
            found = photon_search(typed, limit=5, timeout=3)
        except Exception as err:
            print("map suggestions failed:", err)  # still show the saved ones
            found = []
        already = {a.lower() for a in saved}
        places = []
        for p in found:
            if p["formatted"].lower() not in already and p["formatted"] not in places:
                places.append(p["formatted"])
        # "suggestions" kept for older versions of the page
        return respond(200, {"saved": saved, "places": places, "suggestions": saved})

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

    # 3. Not in the database: look up the real location with the map API
    try:
        result = geocode(raw)
    except Exception as err:
        print("geocode failed:", err)
        return respond(502, {"error": "Address lookup isn't working right now, try again"})
    if result is None:
        log_search(raw, now, "not_found")
        return respond(404, {"error": "couldn't find that address"})

    # 4. Build the new record to save
    item = {
        "address": key,                              # the lookup key
        "formattedAddress": result["formatted"],     # nicely written address
        "lat": Decimal(str(result["lat"])),          # latitude
        "lng": Decimal(str(result["lng"])),          # longitude
        # address details from the map API (DynamoDB doesn't allow empty values, so skip those)
        **{k: result[k] for k in ("street", "city", "state", "postcode", "country") if result.get(k)},
        "source": "google" if MAPS_KEY else "photon",
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

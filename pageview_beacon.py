"""Cookieless, first-party pageview counter for static sites, as one AWS Lambda.

The page sends one small POST per view. This function validates it, drops
obvious crawlers, and writes one row to DynamoDB with a TTL so the table prunes
itself. It sets and reads no cookie, stores no identifier, and no third party
ever sees the traffic -- which is what keeps a plain visit counter outside
consent-banner territory in most jurisdictions.

The pure part (``parse_hit``) is separated from the write so it can be tested
without AWS. ``handler`` is the Lambda entry point.
"""

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "400"))

# A site id is a URL slug: lower-case letters, digits and hyphens, up to 65
# chars. Anything else is rejected before it can reach the table.
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,64}$")

# Obvious non-humans. This is deliberately not exhaustive -- it does not need to
# be. It keeps the numbers shown to a site owner from being quietly inflated by
# crawlers and uptime monitors, which is the only job it has.
BOT = re.compile(
    r"bot|crawl|spider|slurp|bingpreview|headless|lighthouse|pingdom|uptime"
    r"|curl|wget|python-requests",
    re.I,
)

MAX_PATH = 200
MAX_REFERRER = 300

CORS = {
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}


def reply(status, body):
    return {
        "statusCode": status,
        "headers": {**CORS, "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def parse_hit(event, now=None):
    """Turn an API Gateway (HTTP API v2.0) event into a DynamoDB item.

    Returns ``(status, item)``. ``item`` is ``None`` whenever nothing should be
    written: a preflight, a crawler, or a bad request. The caller decides what
    to put in the response from the status alone.
    """
    http = (event.get("requestContext") or {}).get("http") or {}
    if http.get("method") == "OPTIONS":
        return 200, None

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if BOT.search(headers.get("user-agent", "")):
        return 200, None

    # The body is parsed as JSON whatever the Content-Type says. The client
    # sends it as text/plain on purpose -- see beacon.js for why.
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return 400, None
    if not isinstance(body, dict):
        return 400, None

    site_id = str(body.get("site_id") or "").strip().lower()
    if not SLUG.match(site_id):
        return 400, None

    now = now or datetime.now(timezone.utc)
    try:
        screen_w = int(body.get("screen_w") or 0)
    except (TypeError, ValueError):
        screen_w = 0

    return 200, {
        "site_id": site_id,
        # The sort key carries a uuid suffix so two hits in the same millisecond
        # cannot overwrite each other.
        "seen_at": f"{now.isoformat()}#{uuid.uuid4().hex[:8]}",
        "path": str(body.get("path") or "/")[:MAX_PATH],
        "referrer": str(body.get("referrer") or "")[:MAX_REFERRER],
        "screen_w": screen_w,
        "is_mobile": bool(body.get("is_mobile")),
        # DynamoDB TTL: the row disappears on its own after RETENTION_DAYS.
        "ttl": int((now + timedelta(days=RETENTION_DAYS)).timestamp()),
    }


_table = None


def table():
    """The DynamoDB table, created on first use so importing this module
    (for tests) needs no AWS credentials."""
    global _table
    if _table is None:
        import boto3  # noqa: WPS433 -- deferred so tests run without it

        _table = boto3.resource("dynamodb").Table(
            os.environ.get("PAGEVIEW_TABLE", "pageviews")
        )
    return _table


def handler(event, context):
    status, item = parse_hit(event)
    if status != 200:
        return reply(status, {"ok": False})
    if item is None:
        return reply(200, {"ok": True, "counted": False})
    table().put_item(Item=item)
    return reply(200, {"ok": True, "counted": True})

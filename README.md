# pageview-beacon

A cookieless, first-party pageview counter for static sites: one Lambda, one
DynamoDB table, one `<script>` tag. No dependencies beyond `boto3`, which Lambda
already has. Python 3.8+.

## Why

A static site on S3 and CloudFront has no server log worth reading, and the
usual answer is a third-party analytics tag. That brings a cookie, a consent
banner, and a dashboard you query by hand.

Sometimes all you need is *how many people looked at this page, on what kind
of device, and where did they come from* — per site, in your own table, so you
can build a report from it. That is all this does.

- **No cookie, no identifier.** Nothing is set or read on the visitor's
  browser, and nothing stored can be tied to a person. That keeps a plain visit
  counter outside consent-banner territory in most jurisdictions. (Not legal
  advice; check your own.)
- **First-party.** The request goes from the page to your own API. No third
  party sees the traffic.
- **Self-pruning.** Every row carries a DynamoDB TTL, so the table never needs
  a cleanup job.
- **Crawlers are not counted.** A short user-agent pattern keeps the numbers
  you show someone from being quietly inflated by bots and uptime monitors.

## Use

**1. Table.** DynamoDB, partition key `site_id` (string), sort key `seen_at`
(string), TTL enabled on the `ttl` attribute. On-demand capacity.

**2. Function.** Deploy `pageview_beacon.py` as a Python Lambda with handler
`pageview_beacon.handler`. Environment: `PAGEVIEW_TABLE` (the table name),
optionally `RETENTION_DAYS` (default 400) and `ALLOWED_ORIGIN` (default `*`).
The execution role needs `dynamodb:PutItem` on that one table.

**3. Route.** `POST /pageview` on an API Gateway HTTP API, pointing at the
function. Enable CORS for `POST, OPTIONS` with `content-type` allowed.

**4. Page.** Copy `beacon.js` to the end of each page, set `SITE_ID` and
`ENDPOINT`.

Each view writes one row:

```
site_id   rosas-tacos
seen_at   2026-01-01T12:00:00+00:00#3f9a1c2e
path      index.html
referrer  https://example.com/
screen_w  390
is_mobile true
ttl       1801234567
```

Query by `site_id` and a `seen_at` range for a month's numbers.

## The thing that is easy to get wrong

`navigator.sendBeacon` is the right transport for a pageview — it survives the
tab being closed — and it will **return `true` and deliver nothing** if you
send the payload as `application/json`.

`application/json` is not a CORS-simple content type. Cross-origin, it forces a
preflight (an `OPTIONS` request the browser has to make first). A beacon cannot
perform a preflight. The browser queues it, `sendBeacon()` returns `true` —
which only ever meant "queued" — and then drops the request without a word.

So `beacon.js` sends the body as **`text/plain`**, which is a simple request
and needs no preflight, and the Lambda parses the body as JSON whatever the
header says.

The way to catch this class of bug is not to read the return value. Fire the
same event three ways — `fetch` with `keepalive`, XHR, and the beacon — and
count what actually lands in the table. The transport that vanishes is the bug.

## What is checked on the way in

- `site_id` must be a URL slug (`^[a-z0-9][a-z0-9-]{0,64}$`), so a crafted
  value cannot reach the table as a key. It is lower-cased and trimmed.
- The body must be a JSON object. Anything else is a `400`.
- `path` and `referrer` are truncated to 200 and 300 characters.
- The sort key carries a uuid suffix, so two hits in the same millisecond
  cannot overwrite each other.
- Only six fields are ever stored. Extra keys in the payload are ignored.

## Tests

```
python3 -m unittest -v
```

19 tests, no AWS, no network, no test framework: good hit, TTL, same-instant
uniqueness, preflight, five crawler user-agents, `text/plain` body, malformed
and non-object JSON, missing and invalid `site_id`, normalisation, truncation,
defaults, non-numeric width, that nothing personal is stored, and the handler's
write / no-write / 400 / CORS paths with a stubbed table.

## Licence

MIT — see [LICENSE](LICENSE).

Built and used in production by [ATS — Adulis Technology Services](https://ats.adulis.com).

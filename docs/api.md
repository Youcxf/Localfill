# API reference

localfill has two servers. Each has its own address and its own job.

| server | address | what it is for |
|--------|---------|----------------|
| webhook | `http://localhost:8010` | receives alerts from TradingView. Open to the internet through your tunnel |
| dashboard API | `http://localhost:8011/api` | everything the dashboard does. Private, needs a login |

You can also try the dashboard API in your browser at http://localhost:8011/docs, and the machine readable description is at http://localhost:8011/openapi.json (use it to generate an Angular client, see [frontend.md](frontend.md)).

- [Things that are true everywhere](#things-that-are-true-everywhere)
- [Webhook server](#webhook-server)
- [Dashboard API](#dashboard-api): [auth](#auth), [status and settings](#status-and-settings), [connections](#connections), [trading](#trading), [live events](#live-events)
- [Data shapes](#data-shapes)

## Things that are true everywhere

- Bodies and answers are JSON. Field names are `snake_case`.
- Times are ISO 8601 in UTC, like `2026-09-20T21:46:57+00:00`.
- Errors look like `{ "detail": "what went wrong" }`. The exception is a body that does not fit the expected shape on the dashboard API: there `detail` is a list of problems (`loc`, `msg`, `type`), which is FastAPI's normal format.
- The dashboard API uses a **session cookie** called `localfill_session`. You get it from `/api/auth/setup` or `/api/auth/login`, and the browser sends it back by itself. A route that needs it answers `401` without it.

The examples use `curl` with a cookie file. On Windows PowerShell use `curl.exe` (plain `curl` is something else) or the /docs page.

---

## Webhook server

Port 8010. It has two routes and nothing else.

### `POST /hook/{token}`

Where TradingView sends alerts. `{token}` is the `webhook_token` from `GET /api/settings`.

The body is a signal, as JSON. TradingView sends it as `text/plain` when the message is not valid JSON and as `application/json` when it is, and both are accepted.

| field | type | default | notes |
|-------|------|---------|-------|
| `action` | `"buy"`, `"sell"` or `"flatten"` | required | case does not matter |
| `symbol` | string | | required for buy and sell. `MNQ1!` and `CME_MINI:MNQ1!` are turned into `MNQ`, then into the front month contract |
| `qty` | integer, 1 or more | `1` | contracts per account. Ignored when `position` is set |
| `position` | integer | | target position per account (`2` long, `-1` short, `0` flat). Each account reads its real position for the contract and sends only the difference, nothing if it is already there. Market orders only, not allowed with `flatten` |
| `order_type` | `"market"`, `"limit"` or `"stop"` | `"market"` | |
| `price` | number | | required for `limit` and `stop`. Sent to Tradovate as the limit price or the stop price |
| `contract` | string | | exact contract, like `MNQZ6`. Skips the front month lookup |
| `connection` | string | | only this connection (by label) |
| `accounts` | string list | | only these account names or ids, out of the target accounts |
| `sent_at` | ISO date-time | | when the alert was sent. An alert older than the last one accepted for the same symbol, connection and accounts is saved as `duplicate` and not run |
| `is_automated` | boolean | `true` | |

For `flatten`, `symbol` (or `contract`) is optional. Without it every open position on every target account is closed.

Before anything is sent, an alert asking for more than `LOCALFILL_MAX_CONTRACTS` per account (`qty`, or the size of `position`) fails with a warning and sends nothing.

In position mode each account waits after its order until Tradovate's position list shows the target (up to 3 seconds; if it doesn't, the status ends with "position not confirmed yet"). An account holding another month of the same product fails with "still holds ..." and sends nothing.

`flatten` first cancels the account's working orders (only for that contract if one is given), then closes the positions.

Orders (`placeorder`, `liquidateposition`) are never retried after they may have reached Tradovate. If the answer is a timeout or a 5xx, that account's result fails with an error saying the order may or may not have gone through.

```bash
curl -X POST http://localhost:8010/hook/YOUR_TOKEN \
  -H "content-type: application/json" \
  -d '{"action": "buy", "symbol": "MNQ", "qty": 2}'
```

**Answers**

| status | when | body |
|--------|------|------|
| `202` | accepted, the orders are being sent in the background | `{ "signal_id": 12, "status": "pending" }` |
| `200` | an identical signal came in a moment ago (see `LOCALFILL_DEDUPE_SECONDS`), so this one was ignored | `{ "signal_id": 13, "status": "duplicate" }` |
| `400` | the body is not JSON | `{ "detail": "the body is not valid json" }` |
| `404` | wrong token | `{ "detail": "Not Found" }` |
| `413` | body larger than 10 000 bytes | `{ "detail": "payload too large" }` |
| `422` | the signal is not valid | `{ "detail": "symbol is required for buy and sell" }` |

The `202` comes back before anything is sent to Tradovate. To learn what happened, read the signal with `GET /api/signals/{signal_id}` or listen to [`/api/events`](#live-events).

Signals are executed one at a time, in the order they arrived, so a `sell` followed by a `buy` never swap places.

### `GET /health`

`{ "status": "ok", "version": "0.1.0" }`. No token needed. Handy to check that your tunnel works.

---

## Dashboard API

Base address `http://localhost:8011/api`. Everything here needs the session cookie unless it says otherwise.

### Auth

#### `GET /auth/status`

No login needed. Call it when the app starts, it tells you which screen to show.

```json
{ "setup_required": false, "logged_in": true }
```

- `setup_required: true` means nobody has chosen a password yet, show the setup screen.
- `logged_in: false` (with `setup_required: false`) means show the login screen.

#### `POST /auth/setup`

No login needed, but it only works once (`409` afterwards). Chooses the password and logs you in.

```bash
curl -c cookies.txt -X POST http://localhost:8011/api/auth/setup \
  -H "content-type: application/json" -d '{"password": "at least 8 characters"}'
```

Body: `{ "password": "..." }`, at least 8 characters (`422` otherwise). Answer: `{ "setup_required": false, "logged_in": true }` and the session cookie.

#### `POST /auth/login`

No login needed. Body: `{ "password": "..." }`. Answer as for setup. A wrong password is a `401` (after a one second delay). `409` if no password has been chosen yet.

#### `POST /auth/logout`

Ends the session. Answer `204`, no body.

### Status and settings

#### `GET /health`

No login needed. `{ "status": "ok", "version": "0.1.0" }`. This is what the Docker healthcheck calls.

#### `GET /status`

A summary for the top of the dashboard.

```json
{ "version": "0.1.0", "dry_run": false, "connections_total": 1, "connections_online": 1 }
```

`connections_online` counts the connections that are logged in.

#### `GET /settings`

```json
{
  "dry_run": false,
  "webhook_token": "Zp3k...",
  "webhook_path": "/hook/Zp3k...",
  "webhook_url": "https://alerts.example.com/hook/Zp3k..."
}
```

`webhook_url` is `null` unless `LOCALFILL_PUBLIC_URL` is set. Otherwise show `webhook_path` and let the user put their tunnel address in front. The token is a secret, hide it until the user asks to see it.

#### `PATCH /settings`

Body: `{ "dry_run": true }`. Only the fields you send are changed. Answers with the new settings.

In dry run mode alerts are received and logged (with status `dry_run`) but no order is sent. Show a clear banner while it is on.

#### `POST /settings/webhook-token/regenerate`

Makes a new token. **The old webhook URL stops working immediately**, so every TradingView alert has to be updated. Ask the user to confirm. Answers with the new settings.

### Connections

A connection is one Tradovate login. It can see several accounts (`accounts`), and orders go to the ones marked `is_target`.

#### `GET /connections`

```json
[
  {
    "label": "Prop 1",
    "demo": true,
    "auth_mode": "web",
    "authenticated": true,
    "user_id": 42,
    "token_expires_at": "2026-09-20T23:01:24.873203+00:00",
    "seconds_to_expiry": 4465.9,
    "accounts": [
      { "id": 1001, "name": "DEMO1001", "active": true, "is_target": true },
      { "id": 1002, "name": "DEMO1002", "active": true, "is_target": true }
    ],
    "last_error": null
  }
]
```

If `authenticated` is `false`, look at `last_error` and offer a reconnect button. Passwords are never sent back.

#### `POST /connections`

Adds a Tradovate login. localfill logs in first and only saves the connection if that works, so this call can take a few seconds. Answers `201` with the connection (same shape as above).

| field | type | default | notes |
|-------|------|---------|-------|
| `label` | string | required | 1 to 50 characters: letters, digits, space, `_`, `.`, `-`. Must be unique |
| `username` | string | required | |
| `password` | string | required | the Tradovate password |
| `demo` | boolean | `true` | `false` uses Tradovate's live servers |
| `auth_mode` | `"web"` or `"api_key"` | `"web"` | see the README, "How the login works" |
| `cid` | string | | only for `api_key` |
| `api_secret` | string | | only for `api_key` |
| `target_accounts` | string list | `[]` | account names or ids to trade on. Empty means every active account |

```bash
curl -b cookies.txt -X POST http://localhost:8011/api/connections \
  -H "content-type: application/json" \
  -d '{"label": "Prop 1", "username": "me", "password": "secret", "demo": true}'
```

Errors: `409` label already exists, `400` login failed, for example
`{ "detail": "login failed, connection not saved: [Prop 1] login rejected: Incorrect username or password" }`.
Show `detail` to the user as it is.

#### `PATCH /connections/{label}`

Choose which accounts get orders. Body: `{ "target_accounts": ["DEMO1001"] }`. An empty list means all active accounts. Answers with the connection. `404` if the label is unknown.

`label` is part of the address, so encode it (`Prop%201`).

#### `POST /connections/{label}/reconnect`

Tries to log in again, for a connection that failed at startup or lost its login. No body. Answers with the connection, or `400` with the reason. `404` if the label is unknown.

#### `DELETE /connections/{label}`

Logs out and forgets the connection. `204`, or `404`.

### Trading

#### `GET /positions`

Open positions on every target account.

```json
{
  "positions": [
    { "connection": "Prop 1", "account_id": 1001, "account_name": "DEMO1001",
      "contract_id": 1, "contract_name": "MNQZ6", "net_pos": 2.0 }
  ],
  "distinct_contracts": ["MNQZ6"]
}
```

`net_pos` is positive for long and negative for short. If `distinct_contracts` has more than one entry you are in two contract months at once, usually after a rollover. Worth a warning in the UI.

#### `GET /pnl`

Realized profit and loss of the current trade day, per account.

```json
{
  "accounts": [
    {
      "connection": "Prop 1",
      "account_id": 1001,
      "account_name": "DEMO1001",
      "realized_pnl": 40.0,
      "trade_date": "2026-09-20",
      "fill_pairs": [
        { "buy_price": 20003.0, "sell_price": 20013.0, "qty": 2.0, "ts": "2026-09-20T21:47:00.848785Z" }
      ]
    }
  ]
}
```

`realized_pnl` comes straight from Tradovate, in the account's currency. `fill_pairs` are the individual round trips behind it. Either can be `null` or empty if Tradovate did not answer.

#### `GET /signals`

The history, newest first. Query: `limit` (1 to 200, default 50), `offset` (default 0) and `status` (only signals in that state, one of the values in [SignalRecord](#signalrecord)). Answers with a list of [signal records](#signalrecord).

#### `GET /signals/counts`

How many signals are in each state, for tabs above the history. Every state is always there, with `0` when unused.

```json
{ "total": 7, "pending": 0, "done": 3, "partial": 2, "failed": 1, "dry_run": 1, "duplicate": 0, "error": 0 }
```

#### `GET /signals/{id}`

One signal record, or `404`.

#### `POST /signals`

Sends an order from the dashboard. The body is the same as the webhook's. Unlike the webhook, **this waits until every account has answered** and returns the finished record, so it can take a couple of seconds. Show a spinner. It is not affected by duplicate detection.

```bash
curl -b cookies.txt -X POST http://localhost:8011/api/signals \
  -H "content-type: application/json" \
  -d '{"action": "buy", "symbol": "MNQ", "qty": 2}'
```

Answer (a [signal record](#signalrecord)):

```json
{
  "id": 6,
  "received_at": "2026-09-20T21:46:57+00:00",
  "source": "dashboard",
  "payload": {
    "action": "buy", "symbol": "MNQ", "qty": 2, "order_type": "market",
    "price": null, "contract": null, "connection": null, "is_automated": true
  },
  "status": "done",
  "message": null,
  "result": {
    "action": "buy",
    "symbol": "MNQ",
    "contract": "MNQZ6",
    "total": 2,
    "succeeded": 2,
    "failed": 0,
    "results": [
      {
        "connection": "Prop 1",
        "account_id": 1001,
        "account_name": "DEMO1001",
        "success": true,
        "latency_ms": 1.8,
        "order_id": 17,
        "status": "Filled",
        "fill_price": 20003.0,
        "error": null,
        "response": { "orderId": 17 }
      }
    ],
    "warnings": []
  }
}
```

This route always answers `200`, even when the order failed. Look at `status`. A signal that broke before any order was sent looks like this:

```json
{
  "id": 7, "source": "dashboard", "status": "error",
  "message": "tradovate does not know the symbol 'NOPE'",
  "result": null
}
```

(other fields left out)

#### `POST /flatten`

The panic button. Closes positions on every target account. Body (both fields optional):

```json
{ "symbol": "MNQ", "connection": "Prop 1" }
```

Without `symbol` everything is closed. Answers with a signal record, like `POST /signals`. It is the same as sending `{ "action": "flatten" }`.

### Live events

#### `GET /events`

A [server-sent events](https://developer.mozilla.org/docs/Web/API/Server-sent_events) stream. Open it with `EventSource` and the page updates without polling. It needs the session cookie, and `EventSource` sends it by itself when the page comes from the same address (which is the case behind the Angular dev proxy and in production).

Each message is one line of JSON:

```
data: {"type": "signal", "data": { ...a signal record... }}
```

| `type` | `data` | when |
|--------|--------|------|
| `signal` | a [signal record](#signalrecord) | a signal was received (`status: "pending"`) and again each time it changes (`done`, `failed`, ...). Match on `id` to update a row you already have |
| `connections` | `null` | a connection was added, removed, changed or reconnected. Load `GET /connections` again |
| `settings` | `null` | dry run or the token changed. Load `GET /settings` again |

Every 15 seconds a comment line (`: ping`) is sent so nothing closes the idle connection. `EventSource` ignores it.

---

## Data shapes

TypeScript versions of all of these are in [web/src/app/api/types.ts](../web/src/app/api/types.ts).

### SignalRecord

One row of the history.

| field | type | meaning |
|-------|------|---------|
| `id` | integer | |
| `received_at` | string | when it arrived |
| `source` | `"webhook"` or `"dashboard"` | |
| `payload` | object | the signal as received (cleaned up: lower case action, `MNQ1!` became `MNQ`) |
| `status` | string | see below |
| `message` | string or null | a short explanation when something is wrong |
| `result` | SignalResult or null | what happened on the accounts. `null` while pending, for duplicates, dry runs and errors |

**`status`**

| value | meaning | suggested badge |
|-------|---------|-----------------|
| `pending` | received, orders are being sent | grey |
| `done` | every account worked | green |
| `partial` | some accounts worked, some did not | orange |
| `failed` | no account worked, or there was nothing to send to (see `message`) | red |
| `error` | something broke before any order was sent, for example an unknown symbol (see `message`) | red |
| `dry_run` | dry run was on, nothing was sent | blue |
| `duplicate` | identical to a signal a moment ago, ignored | grey |

### SignalResult

| field | type | meaning |
|-------|------|---------|
| `action` | string | |
| `symbol` | string or null | what was asked for |
| `contract` | string or null | the contract that was used, like `MNQZ6` |
| `total` | integer | how many accounts were tried |
| `succeeded`, `failed` | integer | |
| `results` | AccountResult list | one per account |
| `warnings` | string list | problems before sending, like a connection that is offline and was skipped |

### AccountResult

What happened on one Tradovate account.

| field | type | meaning |
|-------|------|---------|
| `connection` | string | the connection label |
| `account_id` | integer | |
| `account_name` | string | |
| `success` | boolean | |
| `latency_ms` | number | how long this account took |
| `order_id` | integer or null | Tradovate's order id |
| `status` | string or null | Tradovate's own order status (`Filled`, `Working`, `Rejected`...). For a flatten: `closed 1 position(s)` or `nothing to close` |
| `fill_price` | number or null | the average price we really got |
| `error` | string or null | why it failed |
| `response` | object or null | Tradovate's raw answer, useful to debug a rejection |

A market order that Tradovate accepted and then rejected (market closed, margin, risk limits) has `success: false` and `status: "Rejected"`. localfill checks the real outcome for a couple of seconds instead of trusting the first answer.

### ConnectionHealth

See [`GET /connections`](#get-connections).

| field | type | meaning |
|-------|------|---------|
| `label` | string | |
| `demo` | boolean | |
| `auth_mode` | string | `web` or `api_key` |
| `authenticated` | boolean | logged in with a token that has not expired |
| `user_id` | integer or null | |
| `token_expires_at` | string or null | tokens are renewed automatically well before this |
| `seconds_to_expiry` | number or null | |
| `accounts` | list | `id`, `name`, `active`, `is_target` |
| `last_error` | string or null | the last login problem |

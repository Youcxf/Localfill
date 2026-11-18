# API reference

localfill has two servers. Each has its own address and its own job.

| server | address | what it is for |
|--------|---------|----------------|
| webhook | `http://localhost:8010` | receives alerts from TradingView. Open to the internet through your tunnel |
| dashboard API | `http://localhost:8011/api` | everything the dashboard does. Private, needs a login |

You can also try the dashboard API in your browser at http://localhost:8011/docs, and the machine readable description is at http://localhost:8011/openapi.json (use it to generate an Angular client, see [frontend.md](frontend.md)).

- [Things that are true everywhere](#things-that-are-true-everywhere)
- [Webhook server](#webhook-server)
- [Dashboard API](#dashboard-api): [auth](#auth), [api tokens](#api-tokens), [status and settings](#status-and-settings), [connections](#connections), [bracket presets](#bracket-presets), [trading](#trading), [batches](#batches), [fills and balances](#fills-and-balances), [kill switch](#kill-switch), [live events](#live-events)
- [Data shapes](#data-shapes)
- [Tradovate fields still to check on Demo](#tradovate-fields-still-to-check-on-demo)

## Things that are true everywhere

- Bodies and answers are JSON. Field names are `snake_case`.
- Times are ISO 8601 in UTC, like `2026-09-20T21:46:57+00:00`.
- Errors look like `{ "detail": "what went wrong" }`. The exception is a body that does not fit the expected shape on the dashboard API: there `detail` is a list of problems (`loc`, `msg`, `type`), which is FastAPI's normal format.
- The dashboard API uses a **session cookie** called `localfill_session`. You get it from `/api/auth/setup` or `/api/auth/login`, and the browser sends it back by itself. A route that needs it answers `401` without it.
- Programs (like a trading bot) use an **API token** instead: `Authorization: Bearer <token>`. Tokens are made on the Settings page (see [API tokens](#api-tokens)) and keep working after a restart. A token works everywhere the cookie does, except on the routes marked **session only**: those answer `403` to a token, so a program can never unblock an account, raise a size limit, add or delete a connection, make tokens, change dry run or the webhook token, or resume after a halt.

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
| `accounts` | string list | | only these account names or ids, out of the target accounts. Every named account gets a result: one that can't be traded gets an error (`account X not found on connection A`, `account X is closed or inactive`, `account X is not a target`, `account X is blocked`) |
| `client_id` | string | | your own id for this signal, up to 100 letters, digits, `_`, `.`, `:` or `-`. A `client_id` that was sent before returns the first signal and sends nothing, whatever its status, so a retry or a restart of the sender never trades twice. Turns off the duplicate and late checks |
| `sent_at` | ISO date-time | | when the alert was sent. An alert older than the last one accepted for the same symbol, connection and accounts is saved as `duplicate` and not run |
| `is_automated` | boolean | `true` | |
| `preset` | string | | name of a [bracket preset](#bracket-presets). Its take profits set the size, so `qty` is not used |
| `take_profits` | list of `{ "qty": int, "points": number }` | | the entry in parts, each leaving at `points` from the fill. Leave `points` out for a part that only has the stop. Wins over the preset's. Without any, the whole `qty` gets just the stop |
| `stop_loss` | number, more than 0 | | the stop, in points from the fill. Wins over the preset's backup stop |
| `stop_price` | number | | the stop as an exact price, instead of `stop_loss` |
| `breakeven_after` | integer, 1 or more | | once take profit number n is reached, the stops of the parts that go further move to the entry |
| `breakeven_ticks` | integer | `0` | how many ticks past the entry that moved stop goes (`2` locks in 2 ticks) |
| `if_open` | `"reverse"`, `"replace"` or `"skip"` | `"reverse"` | what to do when the account already holds the contract, see below |

For `flatten`, `symbol` (or `contract`) is optional. Without it every open position on every target account is closed.

Before anything is sent, an alert asking for more than the server allows per account (`qty`, or the size of `position`) fails with a warning and sends nothing. The limit is the product's own one from `LOCALFILL_MAX_CONTRACTS_PER_SYMBOL` (like `MNQ=48,NQ=3`, where `0` means the product is not allowed), or `LOCALFILL_MAX_CONTRACTS` for a product not listed. After that each account's own limit is checked (set on the Connections page, see [`PUT /connections/{label}/limits`](#put-connectionslabellimits)): an account over it gets an error result and nothing is sent to it, the other accounts still trade.

An account the prop firm has locked (the daily loss limit, see [`GET /accounts`](#get-accounts)) gets the error `account X is locked by the firm: <reason>. nothing sent`. Going flat (`position: 0`, `flatten`) is still sent.

In position mode each account waits after its order until Tradovate's position list shows the target (up to 3 seconds; if it doesn't, the status ends with "position not confirmed yet"). An account holding another month of the same product fails with "still holds ..." and sends nothing. Brackets do the same, whatever `if_open` says.

`flatten` first cancels the account's working orders (only for that contract if one is given), then closes the positions, then reads the position again: `net_pos` in the result is what Tradovate shows afterwards, and the account fails if it is not flat after 3 seconds. Cancelling one leg of a bracket makes Tradovate cancel the other one itself, so a cancel that comes back `TooLate` for an order that is no longer working counts as done, not as an error. Blocked accounts are never flattened, not even by a flatten of everything.

**Brackets.** Any of `preset`, `take_profits`, `stop_loss`, `stop_price`, `breakeven_after`, `breakeven_ticks` or `if_open` turns a buy or sell into one Tradovate order strategy: the entry, plus a take profit and a stop for each part. Tradovate places them right after the fill (or keeps them waiting with a limit or stop entry) and moves stops to breakeven itself, so this keeps working while localfill is off. Every bracket needs a stop, from the alert or the preset's backup stop. Brackets don't work with `position` or `flatten`. The size limit counts all parts together. A part's distances are rounded to the contract's tick size.

- `stop_price` is exact. Tradovate only takes distances, so on a market order the stop starts at a distance and is moved to `stop_price` as soon as the fill is known. That first distance is measured from `price` if the alert sends it (`"price": {{close}}`), otherwise it is the preset's backup or max stop. On a limit or stop entry the distance is measured from `price`, which is exact. A `stop_price` already past the fill keeps the first stop and says so
- the preset's `max_stop_loss` caps every stop, from `stop_loss` or `stop_price`. The alert can't lift it
- breakeven waits for the price, not the fill: a quick touch of the take profit moves the stops even if it didn't fill
- `if_open`: `reverse` closes an opposite position first (with its take profits and stop), a same side one gets the new bracket on top. `replace` closes any position first. `skip` sends nothing to an account that holds the contract (status `skipped, it already holds 2`)
- Tradovate keeps a bracket's take profits and stop working when a plain order closes the position, and either could open a new one. So while a bracket localfill started is still running on an account: a plain buy or sell the other way closes the position and its bracket instead of being sent (`closed 10 with its take profits and stop, instead of sell 10`), `position` going flat or to the other side closes it the same way first, and a plain order on a flat account cancels the leftover orders first. `flatten` cancels them too

Orders (`placeorder`, `startOrderStrategy`, `liquidateposition`) are never retried after they may have reached Tradovate. If the answer is a timeout or a 5xx, that account's result fails with an error saying the order may or may not have gone through.

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
| `200` | its `client_id` was sent before: the first signal, nothing new is sent | `{ "signal_id": 9, "status": "done" }` |
| `200` | localfill is [halted](#kill-switch), a buy or sell is saved as an error and not sent | `{ "signal_id": 14, "status": "error" }` |
| `400` | the body is not JSON | `{ "detail": "the body is not valid json" }` |
| `404` | wrong token | `{ "detail": "Not Found" }` |
| `413` | body larger than 10 000 bytes | `{ "detail": "payload too large" }` |
| `422` | the signal is not valid | `{ "detail": "symbol is required for buy and sell" }` |

The `202` comes back before anything is sent to Tradovate. To learn what happened, read the signal with `GET /api/signals/{signal_id}` or listen to [`/api/events`](#live-events).

Signals are executed one at a time, in the order they arrived, so a `sell` followed by a `buy` never swap places.

### `GET /health`

`{ "status": "ok", "version": "1.4.0" }`. No token needed. Handy to check that your tunnel works.

---

## Dashboard API

Base address `http://localhost:8011/api`. Everything here needs the session cookie or an API token unless it says otherwise.

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

### API tokens

All three are **session only**. Only a sha256 of each token is stored.

#### `GET /tokens`

```json
[{ "id": 1, "name": "trading bot", "created_at": "2026-11-17T21:30:00+00:00", "last_used_at": "2026-11-17T21:36:12+00:00" }]
```

`last_used_at` is updated at most once a minute.

#### `POST /tokens`

Body: `{ "name": "trading bot" }` (1 to 50 characters). Answers `201` with the same fields plus `token`, like `lf_VkC2j...`. **This is the only time the token is shown.** Keep it in the program's settings (a `.env`, never git).

#### `DELETE /tokens/{id}`

Revokes it, `204`. The next call with that token gets `401`. `404` if there is no such token.

### Status and settings

#### `GET /health`

No login needed. `{ "status": "ok", "version": "1.4.0" }`. This is what the Docker healthcheck calls.

#### `GET /status`

A summary for the top of the dashboard, and what a program checks before it trades.

```json
{
  "version": "1.4.0", "dry_run": false, "connections_total": 1, "connections_online": 1, "halted": false,
  "connections": [
    { "label": "Lucid", "online": true, "last_fill_poll": "2026-11-17T21:36:22+00:00", "last_error": null }
  ]
}
```

`connections_online` counts the connections that are logged in. `last_fill_poll` is the last time the fill list was read without an error ([fills](#fills-and-balances)), `null` before the first one. A program should not trade when it is more than about 10 seconds old. `last_error` is the last fill reading or login problem.

#### `GET /settings`

```json
{
  "dry_run": false,
  "webhook_token": "Zp3k...",
  "webhook_path": "/hook/Zp3k...",
  "webhook_url": "https://alerts.example.com/hook/Zp3k...",
  "max_contracts": 10,
  "max_contracts_per_symbol": { "MNQ": 48, "NQ": 3 },
  "daily_flatten_et": "16:44:30"
}
```

`webhook_url` is `null` unless `LOCALFILL_PUBLIC_URL` is set. Otherwise show `webhook_path` and let the user put their tunnel address in front. The token is a secret, hide it until the user asks to see it.

`max_contracts` and `max_contracts_per_symbol` are the server's size limits (`LOCALFILL_MAX_CONTRACTS`, `LOCALFILL_MAX_CONTRACTS_PER_SYMBOL`). `daily_flatten_et` is the New York time of the daily safety flatten (`LOCALFILL_DAILY_FLATTEN_ET`), `null` when it is off. On that time, Monday to Friday, every target account is flattened and saved as a signal with source `schedule`.

#### `PATCH /settings`

**Session only.** Body: `{ "dry_run": true }`. Only the fields you send are changed. Answers with the new settings.

In dry run mode alerts are received and logged (with status `dry_run`) but no order is sent. Show a clear banner while it is on.

#### `POST /settings/webhook-token/regenerate`

**Session only.** Makes a new token. **The old webhook URL stops working immediately**, so every TradingView alert has to be updated. Ask the user to confirm. Answers with the new settings.

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
      { "id": 1001, "name": "DEMO1001", "active": true, "is_target": true, "blocked": false, "is_new": false,
        "max_contracts": { "NQ": 1, "MNQ": 10 } },
      { "id": 1002, "name": "DEMO1002", "active": true, "is_target": false, "blocked": true, "is_new": false,
        "max_contracts": {} }
    ],
    "blocked_accounts": ["DEMO1002"],
    "last_error": null
  }
]
```

If `authenticated` is `false`, look at `last_error` and offer a reconnect button. Passwords are never sent back.

- `blocked`: never traded or flattened, whatever the targets say (your own account on a prop firm login). `blocked_accounts` is the full saved list, also with accounts that are not loaded right now
- `is_new`: the account showed up after the targets were last chosen and is not a target (you bought it, or an evaluation became a funded account). It gets no orders until it is added
- `max_contracts`: the account's own size limits per product, empty when it uses the server's

#### `POST /connections`

**Session only.** Adds a Tradovate login. localfill logs in first and only saves the connection if that works, so this call can take a few seconds. Answers `201` with the connection (same shape as above).

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

Choose which accounts get orders. Body: `{ "target_accounts": ["DEMO1001"] }`. An empty list means all active accounts that exist right now (one that shows up later is not traded until it is chosen). Answers with the connection. `404` if the label is unknown, `400` if the list has a blocked account.

`label` is part of the address, so encode it (`Prop%201`).

#### `POST /connections/{label}/targets/add` and `POST /connections/{label}/targets/remove`

Make one account a target, or stop sending it orders, without sending the whole list. Two people (or you and a program) changing targets at the same time can't undo each other. Body: `{ "account": "DEMO1003" }` (name or id). Answers with the connection. `400` for an unknown or blocked account.

Removing the last target leaves no target at all, never "every account".

#### `PUT /connections/{label}/blocked`

**Session only.** The full list of blocked accounts: `{ "blocked_accounts": ["DEMO1002"] }` (names or ids). A blocked account is never returned as a target, gets an error when an alert names it, is never flattened (not even by a flatten of everything) and its fills are not kept. Blocking an account also takes it out of the targets. Answers with the connection.

Block every account you trade by hand **before** choosing targets.

#### `PUT /connections/{label}/limits`

**Session only**, so a program can't raise them. One account's own size limits, like a prop firm's scaling plan:

```json
{ "account": "DEMO1001", "max_contracts": { "NQ": 1, "MNQ": 10 } }
```

They are checked after the server's limits, so they can only lower them. `0` means that product is not allowed. A product not in the list uses the server's limit. An empty `max_contracts` removes the account's own limits. `400` for an unknown account.

#### `POST /connections/{label}/refresh`

Loads the account list from Tradovate now (it is also reloaded every `LOCALFILL_ACCOUNT_REFRESH_SECONDS`). No body. Answers with the connection: an account you just bought has `is_new: true`. Each new account also fires an `accounts` event with `change: "new"`, and each account that closed one with `change: "closed"` (not for the first load after a restart, so read `is_new` too).

#### `POST /connections/{label}/reconnect`

Tries to log in again, for a connection that failed at startup or lost its login. No body. Answers with the connection, or `400` with the reason. `404` if the label is unknown.

#### `DELETE /connections/{label}`

**Session only.** Logs out and forgets the connection. `204`, or `404`.

### Bracket presets

A preset is a bracket saved under a name. Alerts use it with `"preset": "<name>"`, and fields in the alert win over the preset's (except `max_stop_loss`).

#### `GET /presets`

Every preset, by name.

```json
[
  {
    "name": "nq split",
    "take_profits": [{ "qty": 5, "points": 8 }, { "qty": 5, "points": 20 }],
    "stop_loss": 15,
    "max_stop_loss": 30,
    "breakeven_after": 1,
    "breakeven_ticks": 2,
    "if_open": "reverse"
  }
]
```

| field | meaning |
|-------|---------|
| `take_profits` | the parts, like the alert field. Can be empty: then the alert's `qty` gets only the stop |
| `stop_loss` | backup stop in points, used when the alert sends no stop. `null` means every alert must send one |
| `max_stop_loss` | the farthest stop allowed in points. A farther one from an alert is moved in to it |
| `breakeven_after`, `breakeven_ticks` | like the alert fields |
| `if_open` | like the alert field |

#### `PUT /presets/{name}`

Creates the preset or replaces the one with that name. The body is the preset without `name`. Names are 1 to 50 letters, digits, spaces, dots, dashes or underscores. A preset that can't work is refused with `422`, for example breakeven after a take profit that no part goes further than.

#### `DELETE /presets/{name}`

`204`, or `404`. Alerts that use it fail with "there is no preset called ..." until it exists again.

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

#### `GET /signals/client/{client_id}`

The signal sent with this `client_id`, or `404`. A program that died right after sending checks here what became of it.

#### `POST /signals`

Sends an order from the dashboard (or a program, then `source` is `api`). The body is the same as the webhook's. Unlike the webhook, **this waits until every account has answered** and returns the finished record, so it can take a couple of seconds. Show a spinner. It is not affected by duplicate detection.

With a `client_id` that was sent before, the first record comes back right away and nothing is sent, whatever its status. If it is `error` because localfill stopped while it ran, check the positions yourself: localfill will not send it again.

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

#### `GET /contracts/front`

`?symbol=MNQ` gives the contract it trades right now: `{ "symbol": "MNQ", "name": "MNQZ6", "id": 1 }`. It changes at rollover, so a program can see the switch coming. `404` for an unknown symbol, `503` when no connection is online.

### Batches

Different orders on different accounts, all at the same moment. One `POST /signals` per account would run one after the other (2 to 5 seconds each); a batch runs every order together.

#### `POST /batches`

```json
{
  "client_id": "run1-2026-11-17-1000",
  "orders": [
    { "client_id": "run1-acct12-2026-11-17-entry", "accounts": ["ACCT12"], "action": "buy", "symbol": "MNQ",
      "qty": 16, "take_profits": [{ "qty": 16, "points": 15.75 }], "stop_loss": 29.75, "if_open": "skip" },
    { "client_id": "run1-acct09-2026-11-17-entry", "accounts": ["ACCT09"], "action": "buy", "symbol": "MNQ",
      "qty": 3, "take_profits": [{ "qty": 3, "points": 18.5 }], "stop_loss": 60, "if_open": "skip" }
  ]
}
```

Each order is a signal, like the webhook body, and needs `accounts`. At most 50 orders.

**Everything is checked before anything is sent.** Every order needs `accounts`, no account may be in two orders, and every order's size (server and account limits) and bracket must work. If anything fails the answer is `422` with every reason, nothing is sent and nothing is saved:

```json
{ "detail": ["order 2: asks for 100 contracts, the limit is 10 (LOCALFILL_MAX_CONTRACTS), nothing was sent"] }
```

Then all orders run at once, in one turn of the queue so no other signal slips in between. Each one is saved as its own signal (with `batch_id`), so the history and `client_id` work per order. An unknown, blocked or locked account only fails its own order. In dry run every order is saved as `dry_run`. `409` while localfill is halted.

The answer comes once every order is done, with the orders in the order they were sent:

```json
{ "batch_id": 3, "client_id": "run1-2026-11-17-1000", "status": "done", "received_at": "...", "orders": [ ...signal records... ] }
```

`status` is `done` (every order done), `partial`, `failed`, `dry_run` or `error` (localfill stopped while it ran).

**Sending it again.** A batch `client_id` seen before returns that batch and sends nothing. An order whose own `client_id` was seen before is skipped and its record returned, so a batch that was only half sent (a crash) can be sent again under a new batch `client_id`.

#### `GET /batches/{id}` and `GET /batches/client/{client_id}`

One batch, or `404`.

### Fills and balances

#### `GET /fills`

Every fill localfill has seen, oldest first: entries, and also the take profits and stops that fill later at Tradovate, flattens, and the firm's liquidations. Query: `since` (a `seq`, default 0) and `limit` (1 to 1000, default 500). Answers with [fill records](#fillrecord) whose `seq` is above `since`.

A program keeps the last `seq` it handled and reads on from there, also after its own restart. This history is the source of truth; the `fill` event is only the wake-up call (a slow listener can miss events).

localfill reads Tradovate's fill list every `LOCALFILL_FILL_POLL_SECONDS` (default 1, `0` turns it off). Fills of blocked accounts are not kept. The first time a connection is read its existing fills are saved with `imported: true` and no event, so nothing old looks new. Fills that happened while localfill was off are new when it starts again.

#### `GET /accounts`

The real numbers of every target account (`?all=true`: every account that is not blocked).

```json
[
  {
    "connection": "Lucid", "account_id": 1001, "name": "DEMO1001", "active": true, "is_target": true,
    "cash_balance": 50038.76, "realized_pnl_today": 40.0, "open_pnl": 0.0, "net_liq": 50038.76,
    "trade_date": "2026-11-17", "fees_today": 1.24,
    "positions": [{ "contract": "MNQZ6", "net_pos": 2 }],
    "working_orders": 2,
    "risk": { "locked": false, "reason": null, "admin_action": "Normal", "liquidate_only": null, "user_triggered_liq_only": false },
    "error": null
  }
]
```

- `cash_balance`, `realized_pnl_today`, `open_pnl`, `net_liq` come from Tradovate's cash balance snapshot, with `/cashBalance/list` as the fallback. `realized_pnl_today` is after fees
- `fees_today` adds up the fees of the fills localfill saw on `trade_date`
- `working_orders` counts the orders that can still fill
- `risk` is the prop firm's lock (the daily loss limit), from `/accountRiskStatus/list`. `locked` is true when Tradovate will refuse new orders: an `admin_action` other than `Normal` (and the auto liquidation hold settings), `user_triggered_liq_only`, or `liquidate_only` in the future. Orders to a locked account are refused before anything is sent. Reading this route reads the risk state again, and a change fires an `accounts` event with `change: "locked"` or `"unlocked"`
- `error` says what could not be read, if anything

### Kill switch

#### `POST /halt`

Works with a token. Does four things:

1. saves `halted`, which survives a restart
2. refuses every new buy and sell (webhook, `POST /signals`, batches) until resume. Flattens still work
3. flattens every target account right away, without waiting for the signals in the queue. The queued ones end as `error` with "localfill is halted"
4. once the signal that was running is over, flattens again in case it opened something

Answers `{ "halted": true, "flatten": { ...signal record of the flatten... } }`. Publishes a `settings` event.

#### `POST /resume`

**Session only.** Clears it: `{ "halted": false, "flatten": null }`.

### Live events

#### `GET /events`

A [server-sent events](https://developer.mozilla.org/docs/Web/API/Server-sent_events) stream. Open it with `EventSource` and the page updates without polling. It needs the session cookie, and `EventSource` sends it by itself when the page comes from the same address (which is the case behind the Angular dev proxy and in production). Programs open it with their API token in the `Authorization` header.

Each message is one line of JSON:

```
data: {"type": "signal", "data": { ...a signal record... }}
```

| `type` | `data` | when |
|--------|--------|------|
| `signal` | a [signal record](#signalrecord) | a signal was received (`status: "pending"`) and again each time it changes (`done`, `failed`, ...). Match on `id` to update a row you already have |
| `connections` | `null` | a connection was added, removed, changed or reconnected. Load `GET /connections` again |
| `settings` | `null` | dry run, the token, or halt changed. Load `GET /settings` and `GET /status` again |
| `presets` | `null` | a bracket preset was saved or deleted. Load `GET /presets` again |
| `fill` | a [fill record](#fillrecord) | a new fill, within a couple of seconds of Tradovate. Read `GET /fills?since=` to catch up after a gap |
| `position` | `{ connection, account_id, account_name, contract, net_pos }` | right after a fill, Tradovate's position of that account and contract |
| `accounts` | `{ connection, account_id, name, active, change }` | `change` is `new` (an account showed up), `closed`, `locked` or `unlocked` (the firm's lock, with a `reason`) |

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
| `source` | `"webhook"`, `"dashboard"`, `"api"` or `"schedule"` | `api` is a program with a token, `schedule` the daily flatten |
| `payload` | object | the signal as received (cleaned up: lower case action, `MNQ1!` became `MNQ`) |
| `status` | string | see below |
| `message` | string or null | a short explanation when something is wrong |
| `result` | SignalResult or null | what happened on the accounts. `null` while pending, for duplicates, dry runs and errors |
| `client_id` | string or null | the sender's own id |
| `batch_id` | integer or null | the batch it was sent in |

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
| `qty` | integer or null | contracts per account, for a fixed size (`qty` or a bracket). `null` for `position` and `flatten` |
| `bracket` | string or null | the take profits and stop that were used, like `5 at +8, 5 at +20, stop 12, breakeven after tp 1 +2 ticks` |
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
| `status` | string or null | Tradovate's own order status (`Filled`, `Working`, `Rejected`...). For a flatten: `closed 1 position(s)` or `nothing to close`. Notes are added after a comma, like `Filled, closed 2 first` or `Filled, stop at 19990` |
| `fill_price` | number or null | the average price we really got |
| `error` | string or null | why it failed |
| `response` | object or null | Tradovate's raw answer, useful to debug a rejection |
| `strategy_id` | integer or null | for a bracket, the Tradovate order strategy that holds its take profits and stop. `order_id` is its entry |
| `net_pos` | number or null | for a flatten, the position Tradovate shows afterwards (`0` = flat) |

An account that could not be traded at all (unknown, closed, not a target, blocked, locked, over its own limit) has `success: false`, the reason in `error`, and `account_id` `0` when it is unknown.

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
| `accounts` | list | `id`, `name`, `active`, `is_target`, `blocked`, `is_new`, `max_contracts` |
| `blocked_accounts` | string list | every blocked name or id |
| `last_error` | string or null | the last login problem |

### FillRecord

One fill, see [`GET /fills`](#get-fills).

| field | type | meaning |
|-------|------|---------|
| `seq` | integer | localfill's own number, always growing. Read on from the last one you handled |
| `fill_id` | integer | Tradovate's id, a fill is never stored twice |
| `connection`, `account_id`, `account_name` | | |
| `contract` | string | like `MNQZ6` |
| `action` | `"buy"` or `"sell"` | |
| `qty`, `price` | number | |
| `order_id`, `strategy_id` | integer or null | Tradovate's order, and the bracket it belongs to |
| `role` | string | see below |
| `ts` | string or null | Tradovate's time of the fill |
| `trade_date` | string or null | Tradovate's trade date, like `2026-11-17` |
| `seen_at` | string | when localfill saw it |
| `imported` | boolean | it already existed the first time localfill looked at the connection |
| `fee` | number or null | every fee Tradovate charged for it, `null` when Tradovate gave none (looked up again for a minute) |

**`role`**

| value | meaning |
|-------|---------|
| `entry` | the entry of a bracket localfill started |
| `take_profit`, `stop` | a take profit (limit) or stop of a bracket localfill started |
| `flatten` | localfill closed the position (`flatten`, or closing before a reverse) |
| `plain` | a plain localfill buy or sell |
| `liquidation` | an order localfill did not place, marked `admin` by Tradovate: the firm's risk system |
| `other` | an order localfill did not place: you in Tradovate, or anything else |

A fill whose order localfill doesn't know waits 2 seconds before it counts as `liquidation` or `other`, so an order localfill has just sent has time to be written down.

---

## Tradovate fields still to check on Demo

Checked on a real Lucid eval on Demo (1.4.0, 2026-11-17):

- fills of a bracket entry, its take profit, its stop and a flatten come in with the right role, a second or two after the order
- `/fillFee/list` gives the fee of each fill ($0.50 a side on MNQ there)
- `POST /cashBalance/getcashbalancesnapshot` answers `totalCashValue`, `realizedPnL` (after fees), `openPnL` and `netLiq`
- an account that is not locked has no `adminAction`, `liquidateOnly` or `userTriggeredLiqOnly` in `/accountRiskStatus/list`, which reads as open

These are still built from Tradovate's API description and the mock. Check each one on the first real occasion and fix the code if it differs.

- `/fill/list` without `accountId` returns the fills of every account of the login (the check above only traded one account). If a fill has no `accountId`, localfill reads its order with `/order/items?ids=` to find the account
- `/accountRiskStatus/list` on a locked account: write down the exact `adminAction` Lucid sets on the first daily loss lockout (`OPEN_ACTIONS` in `services/risk.py` lists the ones that do not lock)
- the firm's liquidation order: localfill expects `admin: true` on it. Record the first real one and check

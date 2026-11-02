# localfill

localfill is a small server you run on your own computer. TradingView (or anything else that can send a webhook) sends it an alert, and it places the order on your Tradovate accounts.

It works with prop firm accounts and other Tradovate accounts that have no API access, because it logs in the same way Tradovate's own website does. It does the same job as PickMyTrade or TradersPost, but nothing is hosted: your logins stay on your machine.

> **Status:** the backend and the web dashboard are finished and tested, but so far only against the fake Tradovate that comes with the repository (see [try it without a Tradovate account](#try-it-without-a-tradovate-account)). Start with a demo account.

## What it does

- Buy, sell (market, limit, stop) and flatten, triggered by a webhook
- Several Tradovate logins at once, several accounts per login. One alert becomes one order on every target account, all sent at the same time
- Turns `MNQ` or TradingView's `MNQ1!` into the current front month contract
- Checks what Tradovate really did with each order (filled, rejected...) and gives you the fill price
- Keeps a history of every signal and streams live updates to the dashboard
- Dry run mode: receive and log alerts without sending any order
- Ignores an identical alert that arrives twice within a couple of seconds
- Saved Tradovate passwords are encrypted

## How it fits together

```
TradingView --> tunnel --> :8010  webhook    --+
                                               +--> localfill --> Tradovate
you ----------------------> :8011  dashboard --+
```

localfill runs two servers in one process:

| port | what is on it | who should reach it |
|------|---------------|---------------------|
| 8010 | the webhook, `/hook/<token>` and nothing else | the internet, through a tunnel |
| 8011 | the dashboard and its API | only you |

They are separate on purpose. The webhook server has no other routes, so exposing it does not expose your dashboard. Docker publishes both ports on `127.0.0.1` only.

TradingView cannot reach a normal home computer, so you need a tunnel between the two. See [docs/tunnel.md](docs/tunnel.md).

## Quick start

You need [Docker](https://www.docker.com/products/docker-desktop/) (Docker Desktop on Windows and macOS).

Download or clone this repository, open a terminal in its folder and run:

```bash
docker compose up -d --build
```

Then open http://localhost:8011.

1. The first time, localfill asks you to choose a dashboard password (at least 8 characters). Do this right after starting: whoever gets there first chooses the password.
2. Open **Connections** and add your Tradovate login. localfill logs in before saving anything, so a wrong password is refused. Leave the environment on Demo until you have tested everything, Live trades real accounts.
3. Open **Settings**, turn **Dry run** on while you test, and copy your webhook URL.
4. Send a test order from **Manual order**, or send an alert to the webhook (see [sending an alert](#sending-an-alert)). Every alert shows up in **Signals**, live.

Everything the dashboard does is a call to the API, so you can also use curl or your own scripts. http://localhost:8011/docs lists every endpoint and lets you try them from the browser.

The database and the encryption key live in a Docker volume called `localfill-data`, so they survive restarts and rebuilds.

### Try it without a Tradovate account

The repository includes a fake Tradovate so you can try everything without risking anything. Create a file called `.env` next to `docker-compose.yml` with this line:

```
LOCALFILL_TRADOVATE_DEMO_URL=http://mock:9000/v1
```

and start with `docker compose --profile mock up -d --build`. Now any username and password works when you add a connection (except the username `baduser`, which is refused so you can test errors). You get two fake accounts with fake fills.

## Sending an alert

Your webhook URL is shown on the Settings page. It is your tunnel address plus the path from `GET /api/settings`:

```
https://<your tunnel address>/hook/<your token>
```

Test it from your own machine first (this sends an order, unless dry run is on):

```bash
curl -X POST http://localhost:8010/hook/YOUR_TOKEN \
  -H "content-type: application/json" \
  -d '{"action": "buy", "symbol": "MNQ", "qty": 1}'
```

The answer comes back right away, before any order is sent (TradingView gives up after 3 seconds, so localfill does the trading in the background):

```json
{ "signal_id": 1, "status": "pending" }
```

Open the Signals page (or `GET /api/signals/1`) to see what happened on each account.

### In TradingView

In the alert dialog, under Notifications, tick "Webhook URL" and paste your URL. Put the order in the message box:

```json
{"action": "{{strategy.order.action}}", "symbol": "{{ticker}}", "qty": {{strategy.order.contracts}}}
```

To close everything, for example in an exit alert:

```json
{"action": "flatten", "symbol": "{{ticker}}"}
```

TradingView cannot send custom headers, which is why the secret is part of the URL. Treat the full URL like a password.

### What you can send

| field | default | meaning |
|-------|---------|---------|
| `action` | required | `buy`, `sell` or `flatten`. Upper or lower case both work |
| `symbol` | required for buy and sell | `MNQ`, `MNQ1!` and `CME_MINI:MNQ1!` all mean the current MNQ front month |
| `qty` | `1` | number of contracts, per account |
| `order_type` | `market` | `market`, `limit` or `stop` |
| `price` | | required for `limit` and `stop` |
| `contract` | | an exact contract like `MNQZ6`, used as it is instead of looking up the front month |
| `connection` | | only trade on the login with this label, otherwise all of them |
| `is_automated` | `true` | passed to Tradovate |

For `flatten`, `symbol` is optional: leave it out to close every position on every target account, or give it to close only that contract.

Anything else in the message is ignored, so it is fine to include extra TradingView placeholders.

The full description of every endpoint, with example answers, is in [docs/api.md](docs/api.md).

## Configuration

Settings are environment variables. Put them in a `.env` file next to `docker-compose.yml` (see `.env.example`). Everything is optional.

| variable | default | meaning |
|----------|---------|---------|
| `LOCALFILL_PUBLIC_URL` | | your tunnel address, so the dashboard can show the full webhook URL |
| `LOCALFILL_DEDUPE_SECONDS` | `2` | ignore an identical alert that arrives again within this many seconds. `0` turns it off |
| `LOCALFILL_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `LOCALFILL_TRADOVATE_APP_VERSION` | `3.261030.0` | the Tradovate web client version we pretend to be. Change it if logins start failing after a Tradovate update |
| `LOCALFILL_TRADOVATE_DEMO_URL` | Tradovate's demo API | where demo connections log in |
| `LOCALFILL_TRADOVATE_LIVE_URL` | Tradovate's live API | where live connections log in |
| `LOCALFILL_HTTP_TIMEOUT_SECONDS` | `10` | timeout for each Tradovate request |
| `LOCALFILL_MAX_RETRIES` | `5` | tries per Tradovate request (network errors and 5xx) |
| `LOCALFILL_RETRY_BACKOFF_BASE` | `0.5` | first wait between tries in seconds, doubles each time |
| `LOCALFILL_TOKEN_RENEW_THRESHOLD` | `0.75` | renew the login token after this fraction of its lifetime |
| `LOCALFILL_CONTRACT_CACHE_SECONDS` | `3600` | how long a front month lookup is remembered |
| `LOCALFILL_DATA_DIR` | `data` (`/data` in Docker) | where the database and key are stored |
| `LOCALFILL_STATIC_DIR` | `static` (`/app/static` in Docker) | the built dashboard files, if that folder exists |
| `LOCALFILL_INGRESS_HOST` / `_PORT` | `127.0.0.1` / `8010` | where the webhook server listens |
| `LOCALFILL_ADMIN_HOST` / `_PORT` | `127.0.0.1` / `8011` | where the dashboard listens |

In Docker, change the ports in the `ports:` section of `docker-compose.yml` rather than the two `_PORT` variables.

## Security

- **The token in the webhook URL is the only thing protecting the webhook.** A wrong token gets a plain 404, the same as a page that does not exist. If the URL leaks, make a new one with `POST /api/settings/webhook-token/regenerate` and the old one stops working immediately. Access logs are off so the token never shows up in the container logs.
- **The dashboard has a password** (stored as a scrypt hash) and uses a session cookie that JavaScript cannot read and that is never sent from other sites. It is meant for `localhost`, and there is no HTTPS on it. Do not publish port 8011 to the internet. If you want to reach it from another computer, use a VPN such as Tailscale.
- **Tradovate passwords are encrypted** before they are written to the database. The key (`secret.key`) sits in the same data volume, so this protects you when the database file leaks on its own (a backup, a screenshot), but not against someone who can read the whole volume. Keep the machine safe.
- **Forgot the dashboard password?** Run `docker compose exec localfill python -m app.reset_password`, then `docker compose restart localfill` and choose a new one.
- **Backups:** the `localfill-data` volume holds everything (database and key).

## How the login works

Tradovate sells API access separately, and many prop firm accounts do not include it. Tradovate's own website does not need it though: it logs in by sending an obfuscated password and a signature that its JavaScript computes with a fixed key. localfill builds the same request, so a username and a password are enough. That is the `web` auth mode, and it is the default.

What this means for you:

- It is not an official integration. If Tradovate changes their website, logins can break until localfill is updated. The first thing to try is a newer `LOCALFILL_TRADOVATE_APP_VERSION`.
- Tradovate slows down repeated logins. localfill waits when it is told to, and reports a clear error if Tradovate asks for a captcha (log in on their website once and try again).
- Automated trading, and this way of connecting, may go against Tradovate's terms or your prop firm's rules. Read them before you use this on a funded account. That is your responsibility.

If you do have API access, you can use the official login instead: give the connection `"auth_mode": "api_key"` together with the `cid` and `api_secret` of your API key. It is written from Tradovate's public documentation and has not been tested against a real API key yet.

localfill is not affiliated with Tradovate or TradingView. Trading futures is risky and this software comes with no warranty. Test with a demo account first.

## Development

The backend is Python 3.12 with FastAPI. To work on it without Docker:

```bash
cd api
python -m venv .venv
.venv\Scripts\activate          # macOS and Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

pytest                          # the tests, no Tradovate account needed
ruff check app tests            # lint
python -m app.main              # run it, data goes to api/data
python -m app.devtools.fake_tradovate   # the fake Tradovate, on port 9000
```

The tests run against the fake Tradovate in `app/devtools`, which also fakes fills, positions and P/L.

The dashboard is an Angular app in `web/`. You need Node 22 or 24 (Angular 22 does not support odd numbered releases such as 25). Start the backend first (`python -m app.main`, or `docker compose up -d`), then:

```bash
cd web
npm ci
npm start                       # http://localhost:4200, /api goes to the backend on 8011
npm test                        # the unit tests
npm run build                   # the production build, in web/dist/localfill/browser
```

More in [docs/frontend.md](docs/frontend.md).

```
api/
  app/
    main.py         starts both servers
    config.py       settings (environment variables)
    context.py      builds the things both servers share
    db.py           sqlite and its migrations
    crypto.py       encrypts the saved passwords
    models.py       the shape of every request and answer
    routes/         the http endpoints
    services/       the logic: dispatcher, signals, connections, reports
    tradovate/      login, api client, front month lookup
    devtools/       the fake Tradovate
  tests/
docs/
  api.md            every endpoint
  frontend.md       how the Angular dashboard is built
  tunnel.md         getting TradingView alerts to your computer
web/
  src/app/
    api/            typed api service, live events, login guard
    core/           number and time formatting, shared state
    layout/         sidebar, top bar
    pages/          one folder per screen
    shared/         status badge, dialogs
```

The dashboard only talks to the backend through the API, so you can also build your own, see [docs/frontend.md](docs/frontend.md).

## Not done yet

- Stop loss and take profit attached to an entry (bracket orders)
- A size multiplier per account and risk limits such as a daily loss stop
- Streaming fills and positions from Tradovate over websocket (everything is polled today)
- The official `api_key` login has not been tried against a real key
- The fill pairs shown in `/api/pnl` assume Tradovate only returns the asked account's pairs. That has not been checked on a real account, `realized_pnl` next to it comes straight from Tradovate and is reliable
- Dashboard sessions live in memory, so restarting localfill logs you out
- Nothing has been run against a real Tradovate account yet, only against the fake one

## License

MIT, see [LICENSE](LICENSE).

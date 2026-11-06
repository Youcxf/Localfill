<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark-theme.png">
    <img src="docs/images/logo-light-theme.png" alt="localfill" width="280">
  </picture>
</h1>

A self-hosted server that turns TradingView alerts (or any webhook) into orders on your Tradovate accounts, and copies each one across all of them. Does the same job as PickMyTrade, TradersPost or Tradesyncer, but your logins stay on your machine. Works with prop firm accounts that have with or without Tradovate API access.

> **Current Version** : <ins>1.1.0</ins>

## Features

- **Copy trading:** one alert goes to every target account, on several logins at once, all sent at the same time. Choose the target accounts per login. Each account gets the same `qty`. It copies alerts, not trades you place by hand on Tradovate
- Buy, sell (market, limit, stop) and flatten
- Turns `MNQ` or `MNQ1!` into the current front month contract
- Tracks each order's real result (filled, rejected...) and fill price
- Signal history, with live updates in the dashboard
- Dry run: log alerts without sending orders
- Ignores identical alerts that arrive within 2 seconds
- Saved Tradovate passwords are encrypted

## Quick start

Needs [Docker](https://www.docker.com/products/docker-desktop/).

```bash
docker compose up -d --build
```

Open http://localhost:8011, then:

1. Choose a dashboard password (8+ characters). Do this right away: whoever gets there first sets it.
2. In **Connections**, add your Tradovate login. It is only saved if the login works. **Live trades real accounts**, so stay on Demo while testing.
3. In **Settings**, turn on **Dry run** and copy your webhook URL.
4. Send an order from **Manual order** or the webhook. Results appear in **Signals**.

Data and the encryption key live in the `localfill-data` Docker volume, which survives rebuilds. Back up that volume. API docs: http://localhost:8011/docs and [docs/api.md](docs/api.md).

### Without a Tradovate account

Add this to a `.env` file next to `docker-compose.yml`:

```
LOCALFILL_TRADOVATE_DEMO_URL=http://mock:9000/v1
```

Then run `docker compose --profile mock up -d --build`. Any login works except the username `baduser`, which is refused. You get two fake accounts with fake fills.

## Ports

```
TradingView --> tunnel --> :8010  webhook    --+
                                               +--> localfill --> Tradovate
you ----------------------> :8011  dashboard --+
```

| port | serves | expose to |
|------|--------|-----------|
| 8010 | only `/hook/<token>` | the internet, through a [tunnel](docs/tunnel.md) |
| 8011 | dashboard and API | only you |

Docker publishes both on `127.0.0.1` only. TradingView can't reach a home computer directly, so it needs the tunnel.

## Sending alerts

The webhook URL is on the Settings page: `https://<tunnel address>/hook/<token>`. Test it locally (this places an order unless dry run is on):

```bash
curl -X POST http://localhost:8010/hook/YOUR_TOKEN \
  -H "content-type: application/json" \
  -d '{"action": "buy", "symbol": "MNQ", "qty": 1}'
```

It answers `{ "signal_id": 1, "status": "pending" }` right away and trades in the background, because TradingView gives up after 3 seconds. Per-account results are in **Signals** or `GET /api/signals/1`.

In TradingView, tick "Webhook URL" under Notifications, paste the URL and use this message:

```json
{"action": "{{strategy.order.action}}", "symbol": "{{ticker}}", "qty": {{strategy.order.contracts}}}
```

To close positions: `{"action": "flatten", "symbol": "{{ticker}}"}`

| field | default | meaning |
|-------|---------|---------|
| `action` | required | `buy`, `sell` or `flatten`, any case |
| `symbol` | required for buy and sell | `MNQ`, `MNQ1!` and `CME_MINI:MNQ1!` all mean the MNQ front month. On `flatten`, leave it out to close every position on every target account |
| `qty` | `1` | contracts per account |
| `order_type` | `market` | `market`, `limit` or `stop` |
| `price` | | required for `limit` and `stop` |
| `contract` | | exact contract like `MNQZ6`, skips the front month lookup |
| `connection` | | only trade on the login with this label |
| `is_automated` | `true` | passed to Tradovate |

Other fields are ignored.

## Configuration

Optional environment variables, set in `.env` (see `.env.example`). In Docker, change ports in `docker-compose.yml` instead of the `_PORT` variables.

| variable | default | meaning |
|----------|---------|---------|
| `LOCALFILL_PUBLIC_URL` | | tunnel address, used to show the full webhook URL |
| `LOCALFILL_DEDUPE_SECONDS` | `2` | window for ignoring identical alerts, `0` turns it off |
| `LOCALFILL_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `LOCALFILL_TRADOVATE_APP_VERSION` | `3.261030.0` | Tradovate web client version we send. Try a newer one if logins break |
| `LOCALFILL_TRADOVATE_DEMO_URL` | Tradovate's demo API | where demo connections log in |
| `LOCALFILL_TRADOVATE_LIVE_URL` | Tradovate's live API | where live connections log in |
| `LOCALFILL_HTTP_TIMEOUT_SECONDS` | `10` | timeout per Tradovate request |
| `LOCALFILL_MAX_RETRIES` | `5` | tries per request (network errors and 5xx) |
| `LOCALFILL_RETRY_BACKOFF_BASE` | `0.5` | first retry wait in seconds, doubles each time |
| `LOCALFILL_TOKEN_RENEW_THRESHOLD` | `0.75` | renew the token after this fraction of its lifetime |
| `LOCALFILL_CONTRACT_CACHE_SECONDS` | `3600` | how long a front month lookup is cached |
| `LOCALFILL_DATA_DIR` | `data` (`/data` in Docker) | database and key location |
| `LOCALFILL_STATIC_DIR` | `static` (`/app/static` in Docker) | built dashboard files |
| `LOCALFILL_INGRESS_HOST` / `_PORT` | `127.0.0.1` / `8010` | webhook server address |
| `LOCALFILL_ADMIN_HOST` / `_PORT` | `127.0.0.1` / `8011` | dashboard address |

## Security

- **Webhook:** the token in the URL is its only protection, so treat the URL like a password. A wrong token gets a plain 404, and access logs are off so the token stays out of the logs. If it leaks, call `POST /api/settings/webhook-token/regenerate`.
- **Dashboard:** password stored as a scrypt hash, with an HttpOnly, same-site session cookie. There is no HTTPS, so never publish port 8011. For remote access, use a VPN such as Tailscale.
- **Tradovate passwords:** encrypted, but the key (`secret.key`) is in the same volume. This protects a leaked database file, not a leaked volume.
- **Forgot the dashboard password:** run `docker compose exec localfill python -m app.reset_password`, then `docker compose restart localfill`.

## How the login works

The default `web` mode logs in the way Tradovate's website does, with an obfuscated password and a signature made with a fixed key, so no API subscription is needed. After login it uses the API host Tradovate returns for that user, since some prop firm users have their own.

- This is not an official integration, and a Tradovate website change can break it.
- Repeated logins get throttled. localfill waits when told to. If Tradovate asks for a captcha, log in on their website once and retry.
- **Automated trading and this login method may break Tradovate's terms or your prop firm's rules.** Checking them is your responsibility.

With API access, use the official login instead: set `"auth_mode": "api_key"` plus `cid` and `api_secret` on the connection (not yet tested with a real key).

Not affiliated with Tradovate or TradingView. Futures trading is risky and this software comes with no warranty.

## Development

Backend (Python 3.12, FastAPI):

```bash
cd api
python -m venv .venv
.venv\Scripts\activate          # macOS and Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

pytest                          # runs against the fake Tradovate, no account needed
ruff check app tests            # lint
python -m app.main              # run it, data goes to api/data
python -m app.devtools.fake_tradovate   # fake Tradovate on port 9000
```

Dashboard (Angular, Node 22 or 24, odd releases such as 25 are not supported). Start the backend first:

```bash
cd web
npm ci
npm start                       # http://localhost:4200, /api goes to the backend on 8011
npm test                        # unit tests
npm run build                   # output in web/dist/localfill/browser
```

```
api/app/
  main.py         starts both servers
  config.py       settings
  context.py      things both servers share
  db.py           sqlite and migrations
  crypto.py       password encryption
  models.py       request and answer shapes
  routes/         http endpoints
  services/       dispatcher, signals, connections, reports
  tradovate/      login, api client, front month lookup
  devtools/       fake Tradovate (also fakes fills, positions, P/L)
api/tests/
docs/             api.md, frontend.md, tunnel.md
web/src/app/
  api/            typed api service, live events, login guard
  core/           formatting, shared state
  layout/         sidebar, top bar
  pages/          one folder per screen
  shared/         status badge, dialogs
```

The dashboard only uses the API, so you can build your own: [docs/frontend.md](docs/frontend.md).

## Future features (to add)

- Bracket orders (stop loss and take profit on entry)
- Per-account size multiplier and risk limits such as a daily loss stop
- Copying trades placed by hand on a lead account
- Websocket streaming from Tradovate (everything is polled)
- Testing orders, flatten and P/L on a real account. The fill pairs in `/api/pnl` assume Tradovate only returns the requested account's pairs. `realized_pnl` comes straight from Tradovate
- Dashboard sessions are in memory, so a restart logs you out

## License

MIT, see [LICENSE](LICENSE).

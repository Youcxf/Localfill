# Getting TradingView alerts to your computer

TradingView sends alerts from its own servers to a web address you give it. A computer at home has no public address, so localfill needs a **tunnel**: a small program that keeps a connection open to a service, which gives you a public `https://` address and passes everything it receives on to your computer.

## The rules

- **Point the tunnel at port 8010, never 8011.** 8010 is the webhook and has nothing but `/hook/<token>`. 8011 is your dashboard.
- **The address must not change.** A TradingView alert stores the URL. If the tunnel gets a new random address every time you restart it, every alert stops working without any error. Use one of the options below with a fixed address.
- **The public address has to use the normal https port (443).** TradingView only sends webhooks to port 80 or 443. All the tunnels below do this.
- **TradingView cannot send headers.** That is why the secret is in the URL (`/hook/<token>`) and not in a header.
- **TradingView gives up after 3 seconds.** localfill answers immediately and trades in the background, so this only matters if your tunnel is slow.

## Which one

| | fixed address | cost | you need |
|---|---|---|---|
| Cloudflare Tunnel | yes, your own domain | free, plus a domain (a few dollars a year) | a domain on Cloudflare |
| ngrok | yes, one free static domain per account | free | an ngrok account |
| Tailscale Funnel | yes, `<machine>.<tailnet>.ts.net` | free | a Tailscale account |

If you do not have a domain, ngrok or Tailscale are the quickest. If you do, Cloudflare Tunnel is already wired into `docker-compose.yml`.

Tunnel programs change their menus and command line options now and then. The steps below describe what to do, and the provider's own guide has the current details.

## Cloudflare Tunnel (built into docker-compose)

1. Put a domain on Cloudflare.
2. In the Cloudflare Zero Trust dashboard, create a tunnel (the "cloudflared" type) and copy its token. Their guide is called "Create a remotely-managed tunnel".
3. Add a **public hostname** to the tunnel, for example `alerts.example.com`, and set its service to `http://localfill:8010`. `localfill` is the name of the service in `docker-compose.yml`, the tunnel runs next to it in the same Docker network.
4. In your `.env` file next to `docker-compose.yml`:
   ```
   CLOUDFLARE_TUNNEL_TOKEN=paste the token here
   LOCALFILL_PUBLIC_URL=https://alerts.example.com
   ```
5. Start it:
   ```bash
   docker compose --profile tunnel up -d
   ```

## ngrok

Run ngrok on your computer (not in Docker) and point it at port 8010:

```bash
ngrok http --url=your-name.ngrok-free.app 8010
```

Your free static domain is shown in the ngrok dashboard. Older versions of ngrok call the option `--domain`. Without it you get a new random address on every start, which is fine for a first test and useless for real alerts.

## Tailscale Funnel

Funnel has to be switched on for your tailnet first (Tailscale's admin console explains how). Then:

```bash
tailscale funnel 8010
```

It prints your public address, `https://<machine>.<tailnet>.ts.net`. See `tailscale funnel --help` for running it in the background.

## Check that it works

Tell localfill your public address by setting `LOCALFILL_PUBLIC_URL` in `.env` (then `docker compose up -d`), so the dashboard can show the complete webhook URL. Then, from any computer or your phone on mobile data:

```bash
curl https://your-address/health
```

You should get `{"status":"ok","version":"0.1.0"}`. That proves the whole path works. Now turn on dry run (`PATCH /api/settings` with `{ "dry_run": true }`) and send a test alert:

```bash
curl -X POST https://your-address/hook/YOUR_TOKEN \
  -H "content-type: application/json" \
  -d '{"action": "buy", "symbol": "MNQ", "qty": 1}'
```

The answer is `{"signal_id": 1, "status": "pending"}` and the signal shows up in `GET /api/signals` with status `dry_run`.

## In TradingView

In the alert dialog, under Notifications, tick "Webhook URL" and paste `https://your-address/hook/YOUR_TOKEN`. Put the order in the message box. Examples are in the [README](../README.md#in-tradingview).

## If nothing happens

Go through these in order:

1. **Did the alert fire?** Check TradingView's alert log first.
2. **Does the tunnel work?** `curl https://your-address/health` from outside your network.
3. **Did localfill get it?** `docker compose logs localfill` shows a line for every signal (`signal 7 from webhook: buy MNQ x1`), and `GET /api/signals` lists it. If it is missing, TradingView never reached you: check the URL and the token.
4. **Wrong token?** That is a `404`. Remember that regenerating the token in the dashboard breaks every alert that uses the old one.
5. **Message is not valid?** That is a `400` or `422` with a reason in `detail`. TradingView does not show you the answer, so send the same message with `curl` to read it.
6. **Signal arrived but no order?** Look at its `status` and `message`. `dry_run` means dry run is on. `failed` or `error` says why, and `result.results[].error` has the reason for each account.

## Keep it running

Alerts only work while your computer is on, awake and connected, and Docker is running. On Windows and macOS, tick "Start Docker Desktop when you sign in" in Docker Desktop's settings, and stop the computer from going to sleep. The containers restart by themselves (`restart: unless-stopped`).

If that is too much to babysit, run localfill on a small always-on machine or a VPS instead. It behaves the same way. Just remember that anything you expose to the internet should be port 8010 only.

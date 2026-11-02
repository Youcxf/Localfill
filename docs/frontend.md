# The dashboard

The dashboard is an Angular app in [`web/`](../web). It only talks to the backend over HTTP, so this page is also the guide if you want to build a different frontend.

- [How the two fit together](#how-the-two-fit-together)
- [Run it while you work on it](#run-it-while-you-work-on-it)
- [How the code is organized](#how-the-code-is-organized)
- [Screens and the endpoints they use](#screens-and-the-endpoints-they-use)
- [Log in and log out](#log-in-and-log-out)
- [Live updates](#live-updates)
- [Look and feel](#look-and-feel)
- [Tests](#tests)
- [Build it and serve it from the container](#build-it-and-serve-it-from-the-container)
- [Working without a Tradovate account](#working-without-a-tradovate-account)
- [Building your own frontend](#building-your-own-frontend)

## How the two fit together

The backend is a JSON API on port **8011**. In production the container serves the built Angular files from that same port, so the app and the API share one address. That matters because login uses a **session cookie**: same address means the browser sends it by itself, and the app never handles a token or needs CORS.

You get the same thing in development with the Angular dev server's proxy (`web/proxy.conf.json`): the app runs on `localhost:4200` and every request that starts with `/api` is passed on to port 8011.

The API reference is in [api.md](api.md).

## Run it while you work on it

Use Node 22 or 24. Angular 22 supports Node 22.22.3 or newer, 24.15 or newer and 26 or newer, but not odd numbered releases such as 25 (check with `node --version`).

Start the backend first. With Docker, from the repository root:

```bash
docker compose --profile mock up -d --build
```

(add `LOCALFILL_TRADOVATE_DEMO_URL=http://mock:9000/v1` to `.env` first, see [working without a Tradovate account](#working-without-a-tradovate-account)). Without Docker, see the development section of the readme.

Then, in another terminal:

```bash
cd web
npm ci
npm start
```

Open http://localhost:4200. The page reloads when you save a file.

## How the code is organized

```
web/src/
  index.html, styles.css    the page, and the colors, fonts and shared classes
  app/
    app.config.ts           http client and router
    app.routes.ts           every route, and which need a login
    api/
      types.ts              an interface for every request and answer
      api.service.ts        one method per endpoint. Components call this and never build urls
      events.service.ts     the live event stream as an RxJS observable
      auth.ts               the route guard and the http interceptor
    core/                   number and time formatting, the shared status
    layout/                 shell, sidebar, top bar
    pages/                  one folder per screen
    shared/                 status badge, the add connection and flatten dialogs
```

A few habits that keep it easy to follow:

- Standalone components, signals for state, `@if` and `@for` in templates. No zone.js.
- Each page that turns api data into words and numbers keeps that in a small plain file next to it (`signal-rows.ts`, `position-groups.ts`, `pnl-accounts.ts`, `connection-cards.ts`). They are plain functions, which makes them easy to read and to test.
- No UI library. The look is a few hundred lines of CSS in `styles.css` plus a small stylesheet per component.

## Screens and the endpoints they use

| screen | what it shows | endpoints |
|--------|---------------|-----------|
| Setup, Login | choose or enter the password | `GET /auth/status`, `POST /auth/setup`, `POST /auth/login` |
| Top bar and sidebar | how many connections are online, a **Dry run** bar with a button to turn it off, whether live updates are connected, logout | `GET /status`, `POST /auth/logout`, events `settings` |
| Signals | the history with a tab and a count per status. Click a row for the per-account results, fill prices, errors and Tradovate's raw answer | `GET /signals`, `GET /signals/counts`, events `signal` |
| Connections | list of logins with their accounts. Add, remove, reconnect, and tick which accounts receive orders | `GET /connections`, `POST /connections`, `PATCH /connections/{label}`, `POST /connections/{label}/reconnect`, `DELETE /connections/{label}` |
| Positions | open positions grouped by contract, a notice when you are in two contract months, and flatten per contract or for everything | `GET /positions`, `POST /flatten` |
| P/L | realized P/L of the day in one big figure, per account, and the round trips of the account you pick | `GET /pnl` |
| Manual order | buy, sell or flatten from the dashboard, and the result per account | `POST /signals` |
| Settings | dry run, the webhook URL (hidden until you reveal it), regenerate it, ready made TradingView messages | `GET /settings`, `PATCH /settings`, `POST /settings/webhook-token/regenerate` |

## Log in and log out

The router guard (`authGuard` in `api/auth.ts`) calls `GET /api/auth/status` before it shows any page. It answers with two booleans:

| `setup_required` | `logged_in` | shows |
|------------------|-------------|-------|
| `true` | | the setup screen: choose a password |
| `false` | `false` | the login screen |
| `false` | `true` | the dashboard |

`authInterceptor` sends the user back to `/login` whenever any call answers `401`, which happens when the session ends or the backend restarts (sessions live in memory). A wrong password on the login form is also a `401`, so the interceptor leaves that one alone and the form shows the error.

## Live updates

The app does not poll. After login the top bar, the Signals page and the Connections page listen to one shared stream (`EventsService.events$`):

| event | the app then |
|-------|--------------|
| `signal` | adds the signal to the top of the history, or replaces the row with the same `id` |
| `connections` | loads the connections again |
| `settings` | loads the settings again (dry run or the token changed) |

A signal event arrives twice for a normal alert: first with `status: "pending"`, then with the final status once Tradovate has answered. That is why the table replaces a row instead of adding a second one. The stream reconnects by itself, and the top bar says "Reconnecting…" while it is down. Details are in [api.md](api.md#live-events).

## Look and feel

- All colors, fonts and sizes are variables at the top of `web/src/styles.css`. Change them there.
- Fonts are installed from npm (`@fontsource/*`) and served by the app itself, so opening the dashboard never contacts another site: Rufina for titles and the menu, Libre Franklin for text, Chivo Mono for numbers.
- The logo is `logo.png` and the round icon is `icon.png`, both in the repository root. `npm start` and `npm run build` copy them into the app (the copies in `web/public` are not committed), so to change one you replace the file in the root. The icon is used for the browser tab, the home screen icon on iOS, and the tab of the API docs page (`/docs`). If your new logo has another size or sits somewhere else in its file, update the numbers at the top of `web/src/app/shared/logo.ts`.
- The blue is `#257fff`. State is never shown by color alone: every status badge, and every "long" or "short", also says the word.
- Times are shown in UTC, as the API sends them.

## Tests

```bash
cd web
npm test
```

The unit tests cover the plain functions that decide what the screens say (formatting, signal rows and details, position groups and rollover notice, P/L totals, connection cards) and the status badge. They run in a few seconds and need no backend.

## Build it and serve it from the container

```bash
cd web
npm run build
```

The files land in `web/dist/localfill/browser`. `docker compose up -d --build` does this for you: the `Dockerfile` has a Node stage that builds the app and a line that copies the result into the image, so nobody needs Node to run localfill.

If you are working on the frontend and want the container to serve your own build without rebuilding the image, uncomment the volume line in `docker-compose.yml`:

```yaml
      - ./web/dist/localfill/browser:/app/static:ro
```

then `docker compose up -d`. Every `npm run build` is picked up on the next page load.

The server sends `index.html` for any address that is not a file, so refreshing the page on `/connections` or opening a deep link works with Angular's router. Only addresses that start with `/api` are never rewritten, so a typo there is a real 404.

## Working without a Tradovate account

The repository has a fake Tradovate, so you can work on the dashboard without a real login, and without any risk. Create a file called `.env` in the repository root:

```
LOCALFILL_TRADOVATE_DEMO_URL=http://mock:9000/v1
```

and start everything with:

```bash
docker compose --profile mock up -d --build
```

Now add a connection with any username and password. You get two fake accounts (`DEMO1001`, `DEMO1002`), orders fill instantly and positions and P/L behave like real ones. The username `baduser` is refused, so you can see the error message. Send test alerts from the Manual order page or with `curl`, see the readme.

## Building your own frontend

Everything the dashboard does goes through the API, so you can replace it. Point the container at your own build (see above), or run yours anywhere behind a proxy that keeps `/api` on the same address. `web/src/app/api/` is a typed client you can copy as it is.

Things worth knowing:

- **Field names are `snake_case`** (`account_name`, `net_pos`). Keep them, do not convert.
- **`POST /connections` and `POST /signals` are slow on purpose.** The first logs in to Tradovate, the second waits for every account to answer. Disable the button and show progress. Give a login a few seconds.
- **`POST /signals` answers `200` even when the order failed.** Check `status`, not the http status.
- **Show `detail` from errors as it is.** For example a wrong Tradovate password comes back as a `400` with a readable `detail`.
- **Encode labels in urls.** A connection can be called `Prop 1`. `api.service.ts` already uses `encodeURIComponent`.
- **The webhook token is a secret.** Hide it until the user clicks to reveal it, and ask for confirmation before regenerating it, because that breaks every TradingView alert that uses the old URL.
- **Make dry run obvious.** While it is on, alerts are logged but nothing is traded.
- **Confirm before flatten.** It closes real positions on every target account.
- **Do not add `withCredentials`.** The app and the API share one address, so cookies are sent automatically.

If you prefer a generated client, [ng-openapi-gen](https://www.npmjs.com/package/ng-openapi-gen) works against the running backend:

```bash
npx ng-openapi-gen --input http://localhost:8011/openapi.json --output src/app/api-generated
```

It produces the same models plus a service per group of endpoints. An event stream is not part of an OpenAPI description, so `EventsService` is still useful.

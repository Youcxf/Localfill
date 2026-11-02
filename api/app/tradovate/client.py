import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone

import aiohttp

from app.models import AccountInfo, ConnectionHealth
from app.tradovate.auth import build_login_payload

logger = logging.getLogger(__name__)


# all our errors inherit from TradovateError so callers can catch just that one
class TradovateError(Exception):
    pass


# login problems: wrong password, captcha, throttled...
class TradovateAuthError(TradovateError):
    pass


# a request failed after all the retries
class TradovateRequestError(TradovateError):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


# tradovate does not always answer with json (some errors are plain text)
def safe_json(body):
    try:
        return json.loads(body)
    except ValueError:
        return {"_raw_text": body.decode("utf-8", errors="replace")}


# one tradovate login and the accounts that belong to it
class TradovateConnection:
    def __init__(self, config, settings):
        self.config = config
        self.settings = settings
        self.session = None
        self.access_token = None
        self.issued_at = None
        self.expires_at = None
        self.user_id = None
        # account id -> name
        self.account_names = {}
        # name or id (as text) -> account id
        self.account_ids = {}
        # account id -> is the account active
        self.account_active = {}
        self.last_error = None
        self.renewal_task = None
        self.auth_lock = asyncio.Lock()
        # set after login when tradovate says this user lives on another host
        self.api_url = None

    @property
    def label(self):
        return self.config.label

    # where we log in
    @property
    def login_url(self):
        if self.config.demo:
            url = self.settings.tradovate_demo_url
        else:
            url = self.settings.tradovate_live_url
        return url.rstrip("/") + "/"

    # where everything else goes
    @property
    def base_url(self):
        return self.api_url or self.login_url

    # some users (prop firms like lucid) have their own host, the login answer tells us
    # which one. the normal host answers 404 for them even with a good token
    def use_api_hosts(self, hosts):
        host = (hosts or {}).get("demo" if self.config.demo else "live")
        self.api_url = f"https://{host}/v1/" if host else None

    @property
    def authenticated(self):
        if not self.access_token:
            return False
        if self.expires_at and self.expires_at < datetime.now(timezone.utc):
            return False
        return True

    # ---------- start / stop ----------

    async def start(self):
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
            self.session = aiohttp.ClientSession(timeout=timeout)
        await self.authenticate()
        await self.refresh_accounts()
        if self.renewal_task is None or self.renewal_task.done():
            self.renewal_task = asyncio.create_task(self.renewal_loop())

    async def stop(self):
        if self.renewal_task:
            self.renewal_task.cancel()
            try:
                await self.renewal_task
            except (asyncio.CancelledError, Exception):
                pass
            self.renewal_task = None
        if self.session:
            await self.session.close()
            self.session = None
        self.access_token = None

    # ---------- login ----------

    def auth_failed(self, message):
        self.last_error = message
        return TradovateAuthError(f"[{self.label}] {message}")

    # stale_token: the token a failed request was using. if the token has changed
    # since then, someone else already logged in again and we can skip
    async def authenticate(self, stale_token=None):
        async with self.auth_lock:
            if stale_token is not None and self.access_token != stale_token:
                return

            data = {}
            p_ticket = None
            for _ in range(3):
                payload = build_login_payload(self.config, self.settings.tradovate_app_version, p_ticket)
                try:
                    data = await self.raw_post("/auth/accesstokenrequest", payload, base=self.login_url)
                except TradovateRequestError as exc:
                    raise self.auth_failed(f"login failed: {exc}") from exc

                if data.get("p-captcha"):
                    raise self.auth_failed("tradovate wants a captcha, log in on their website once and try again")

                # too many logins: tradovate tells us to wait and send the ticket back
                ticket = data.get("p-ticket")
                if not ticket:
                    break
                wait = data.get("p-time", 5)
                if wait > 60:
                    raise self.auth_failed(f"logins are throttled for {wait} seconds, try again later")
                logger.warning("[%s] login throttled, waiting %s seconds", self.label, wait)
                await asyncio.sleep(wait)
                p_ticket = ticket

            token = data.get("accessToken")
            if not token:
                message = data.get("errorText") or f"no accessToken in the answer: {data}"
                raise self.auth_failed(f"login rejected: {message}")

            self.access_token = token
            self.issued_at = datetime.now(timezone.utc)
            self.expires_at = self.parse_time(data.get("expirationTime"))
            self.user_id = data.get("userId")
            self.use_api_hosts(data.get("apiHosts"))
            self.last_error = None
            logger.info("[%s] logged in (user %s, expires %s)", self.label, self.user_id, self.expires_at)

    @staticmethod
    def parse_time(text):
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("could not read expirationTime: %s", text)
            return None

    # ---------- token renewal ----------

    async def renewal_loop(self):
        while True:
            try:
                await asyncio.sleep(max(self.seconds_until_renewal(), 5.0))
                await self.renew_token()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[%s] token renewal failed, trying again in 30s", self.label)
                await asyncio.sleep(30)

    def seconds_until_renewal(self):
        if not self.issued_at or not self.expires_at:
            return 60.0
        lifetime = (self.expires_at - self.issued_at).total_seconds()
        renew_at = self.issued_at + timedelta(seconds=lifetime * self.settings.token_renew_threshold)
        return (renew_at - datetime.now(timezone.utc)).total_seconds()

    async def renew_token(self):
        try:
            data = await self.raw_post("/auth/renewaccesstoken", {}, use_auth=True)
            token = data.get("accessToken")
            if not token:
                raise TradovateRequestError(f"renewal gave no token: {data}")
            self.access_token = token
            self.issued_at = datetime.now(timezone.utc)
            self.expires_at = self.parse_time(data.get("expirationTime"))
            logger.info("[%s] token renewed, expires %s", self.label, self.expires_at)
        except TradovateRequestError:
            logger.warning("[%s] token renewal failed, logging in again", self.label)
            await self.authenticate()

    # ---------- accounts ----------

    async def refresh_accounts(self):
        accounts = await self.request("GET", "/account/list")
        self.account_names.clear()
        self.account_ids.clear()
        self.account_active.clear()
        for account in accounts or []:
            account_id = account.get("id")
            if account_id is None:
                continue
            name = account.get("name", str(account_id))
            self.account_names[account_id] = name
            self.account_ids[name] = account_id
            self.account_ids[str(account_id)] = account_id
            self.account_active[account_id] = account.get("active", True)
        logger.info("[%s] found %d accounts", self.label, len(self.account_names))

    # the account ids that should receive orders
    def target_account_ids(self):
        if not self.config.target_accounts:
            # nothing picked: use every active account
            return [i for i, active in self.account_active.items() if active]
        found = []
        for key in self.config.target_accounts:
            account_id = self.account_ids.get(key)
            if account_id is None:
                logger.warning("[%s] target account '%s' not found", self.label, key)
                continue
            found.append(account_id)
        return found

    # ---------- http ----------

    async def raw_post(self, path, payload, use_auth=False, base=None):
        if self.session is None:
            raise TradovateRequestError("connection is not started")
        headers = {"Accept": "application/json"}
        if use_auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        try:
            url = (base or self.base_url) + path.lstrip("/")
            async with self.session.post(url, json=payload, headers=headers) as resp:
                body = await resp.read()
                data = safe_json(body) if body else {}
                if resp.status >= 400:
                    raise TradovateRequestError(f"{path} -> HTTP {resp.status}: {data}", status=resp.status, payload=data)
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TradovateRequestError(f"{path}: network error: {exc}") from exc

    # a normal api call. logs in again on a 401 and retries with a growing delay
    async def request(self, method, path, json_body=None):
        if self.session is None:
            raise TradovateRequestError("connection is not started")
        # the base url ends with /v1/ so the path must not start with a slash
        path = path.lstrip("/")

        last_error = None
        logged_in_again = False

        for attempt in range(self.settings.max_retries):
            token_used = self.access_token
            headers = {"Accept": "application/json"}
            if token_used:
                headers["Authorization"] = f"Bearer {token_used}"

            try:
                async with self.session.request(method, self.base_url + path, json=json_body, headers=headers) as resp:
                    body = await resp.read()
                    data = safe_json(body) if body else None

                    if resp.status == 401:
                        last_error = TradovateRequestError(f"{path} -> HTTP 401: {data}", status=401, payload=data)
                        # log in again once, if it still fails the token is not the problem
                        if logged_in_again:
                            raise last_error
                        logger.warning("[%s] 401 on %s, logging in again", self.label, path)
                        logged_in_again = True
                        await self.authenticate(stale_token=token_used)
                        continue

                    if resp.status >= 500:
                        last_error = TradovateRequestError(f"{path} -> HTTP {resp.status}: {data}", status=resp.status, payload=data)
                        await self.sleep_backoff(attempt)
                        continue

                    # other 4xx errors will not get better by retrying
                    if resp.status >= 400:
                        raise TradovateRequestError(f"{path} -> HTTP {resp.status}: {data}", status=resp.status, payload=data)

                    return data

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                logger.warning("[%s] network error on %s (try %d/%d): %s", self.label, path, attempt + 1, self.settings.max_retries, exc)
                await self.sleep_backoff(attempt)

        raise TradovateRequestError(f"{path} failed after {self.settings.max_retries} tries: {last_error}")

    async def sleep_backoff(self, attempt):
        base = self.settings.retry_backoff_base
        await asyncio.sleep(base * (2 ** attempt) + random.uniform(0, base))

    # ---------- status ----------

    def health(self):
        seconds_left = None
        if self.expires_at:
            seconds_left = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
        targets = set(self.target_account_ids())
        accounts = [
            AccountInfo(id=i, name=name, active=self.account_active.get(i, True), is_target=i in targets)
            for i, name in self.account_names.items()
        ]
        return ConnectionHealth(
            label=self.label,
            demo=self.config.demo,
            auth_mode=self.config.auth_mode,
            authenticated=self.authenticated,
            user_id=self.user_id,
            token_expires_at=self.expires_at.isoformat() if self.expires_at else None,
            seconds_to_expiry=seconds_left,
            accounts=accounts,
            last_error=self.last_error,
        )

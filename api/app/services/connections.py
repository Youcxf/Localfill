import asyncio
import json
import logging
from datetime import datetime, timezone

from app.models import ConnectionConfig
from app.tradovate.client import TradovateConnection, TradovateError

logger = logging.getLogger(__name__)


# keeps the list of tradovate logins: the live ones in memory, the saved ones in the database
class ConnectionManager:
    def __init__(self, db, crypto, settings, events=None):
        self.db = db
        self.crypto = crypto
        self.settings = settings
        # where "accounts" events go (an account showed up or closed)
        self.events = events
        # label -> TradovateConnection
        self.live = {}
        self.reconnect_task = None
        self.refresh_task = None

    # ---------- database <-> config ----------

    def config_from_row(self, row):
        api_secret = None
        if row["api_secret_enc"]:
            api_secret = self.crypto.decrypt(row["api_secret_enc"])
        return ConnectionConfig(
            label=row["label"],
            username=row["username"],
            password=self.crypto.decrypt(row["password_enc"]),
            demo=bool(row["demo"]),
            auth_mode=row["auth_mode"],
            cid=row["cid"],
            api_secret=api_secret,
            device_id=row["device_id"],
            target_accounts=json.loads(row["target_accounts"]),
            known_accounts=json.loads(row["known_accounts"]) if row.get("known_accounts") else None,
            blocked_accounts=json.loads(row["blocked_accounts"]) if row.get("blocked_accounts") else [],
            account_limits=json.loads(row["account_limits"]) if row.get("account_limits") else {},
        )

    async def save(self, config):
        api_secret_enc = self.crypto.encrypt(config.api_secret) if config.api_secret else None
        # upsert, so saving an existing connection keeps its place in the list
        await self.db.execute(
            "insert into connections "
            "(label, username, password_enc, demo, auth_mode, cid, api_secret_enc, device_id, target_accounts, known_accounts, "
            "blocked_accounts, account_limits, created_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "on conflict(label) do update set "
            "username = excluded.username, password_enc = excluded.password_enc, demo = excluded.demo, "
            "auth_mode = excluded.auth_mode, cid = excluded.cid, api_secret_enc = excluded.api_secret_enc, "
            "device_id = excluded.device_id, target_accounts = excluded.target_accounts, known_accounts = excluded.known_accounts, "
            "blocked_accounts = excluded.blocked_accounts, account_limits = excluded.account_limits",
            (
                config.label,
                config.username,
                self.crypto.encrypt(config.password),
                int(config.demo),
                config.auth_mode,
                config.cid,
                api_secret_enc,
                config.device_id,
                json.dumps(config.target_accounts),
                json.dumps(config.known_accounts) if config.known_accounts is not None else None,
                json.dumps(config.blocked_accounts),
                json.dumps(config.account_limits),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )

    # ---------- start up / shut down ----------

    # log in to every saved connection at the same time
    async def load_saved(self):
        rows = await self.db.fetch_all("select * from connections order by rowid")
        for row in rows:
            config = self.config_from_row(row)
            self.live[config.label] = self.make_connection(config)
        await asyncio.gather(*(self.start_one(conn) for conn in self.live.values()))

    def make_connection(self, config):
        conn = TradovateConnection(config, self.settings)
        conn.on_accounts_changed = self.accounts_changed
        return conn

    # an account showed up (you bought one) or closed: tell whoever listens to /events
    def accounts_changed(self, conn, changes):
        for change in changes:
            logger.info("[%s] account %s: %s", conn.label, change["name"], change["change"])
            if self.events:
                self.events.publish("accounts", change)

    # remembers which accounts exist right now, see ConnectionConfig.known_accounts
    async def remember_accounts(self, conn):
        conn.config.known_accounts = sorted(conn.account_names.values())
        await self.save(conn.config)

    # a failed login does not stop the app, the connection stays in the list with its error
    async def start_one(self, conn):
        try:
            await conn.start()
            # saved before this feature existed: remember today's accounts once
            if conn.config.known_accounts is None:
                await self.remember_accounts(conn)
        except TradovateError as exc:
            logger.error("[%s] could not start: %s", conn.label, exc)
        except Exception:
            logger.exception("[%s] could not start", conn.label)

    # a connection that failed to log in (tradovate down, wifi gone...) would stay offline and
    # miss every signal. this tries those again in the background
    def start_background(self):
        if self.settings.reconnect_seconds > 0 and self.reconnect_task is None:
            self.reconnect_task = asyncio.create_task(self.reconnect_loop())
        if self.settings.account_refresh_seconds > 0 and self.refresh_task is None:
            self.refresh_task = asyncio.create_task(self.refresh_loop())

    # reloads the account list now and then, so a closed or blown account stops getting orders
    async def refresh_loop(self):
        while True:
            await asyncio.sleep(self.settings.account_refresh_seconds)
            for conn in [c for c in list(self.live.values()) if c.online]:
                try:
                    await conn.refresh_accounts()
                except Exception as exc:
                    logger.warning("[%s] could not reload the accounts: %s", conn.label, exc)

    async def reconnect_loop(self):
        while True:
            await asyncio.sleep(self.settings.reconnect_seconds)
            offline = [conn for conn in list(self.live.values()) if not conn.online and not conn.needs_user]
            for conn in offline:
                logger.info("[%s] offline, trying to log in again", conn.label)
                await self.start_one(conn)

    async def stop_all(self):
        for task in (self.reconnect_task, self.refresh_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.reconnect_task = None
        self.refresh_task = None
        await asyncio.gather(*(conn.stop() for conn in self.live.values()), return_exceptions=True)

    # ---------- changes made from the dashboard ----------

    # logs in first, only saves if the login worked
    async def add(self, config):
        if config.label in self.live:
            raise ValueError(f"a connection called '{config.label}' already exists")
        conn = self.make_connection(config)
        try:
            await conn.start()
        except TradovateError:
            await conn.stop()
            raise
        config.known_accounts = sorted(conn.account_names.values())
        await self.save(config)
        self.live[config.label] = conn
        logger.info("[%s] added", config.label)
        return conn

    async def remove(self, label):
        conn = self.live.pop(label, None)
        if conn is None:
            return False
        await conn.stop()
        await self.db.execute("delete from connections where label = ?", (label,))
        logger.info("[%s] removed", label)
        return True

    # raises ValueError when a blocked account is in the list
    async def set_targets(self, label, targets):
        conn = self.live.get(label)
        if conn is None:
            return None
        for key in targets:
            if self.is_blocked_key(conn, key):
                raise ValueError(f"account {key} is blocked, unblock it first")
        conn.config.target_accounts = targets
        # choosing the targets again also accepts the accounts that exist now
        await self.remember_accounts(conn)
        return conn

    def is_blocked_key(self, conn, key):
        return key in conn.config.blocked_accounts or conn.account_ids.get(key) in conn.blocked_ids()

    # the targets as an explicit list of names, also when "every account" is picked
    def explicit_targets(self, conn):
        if conn.config.target_accounts:
            return list(conn.config.target_accounts)
        return [conn.account_names[i] for i in conn.target_account_ids()]

    # makes one more account a target, the others stay as they are.
    # raises ValueError for an unknown or blocked account
    async def add_target(self, label, key):
        conn = self.live.get(label)
        if conn is None:
            return None
        account_id = conn.account_ids.get(key)
        if account_id is None:
            raise ValueError(f"account {key} not found on connection {label}")
        if conn.is_blocked(account_id):
            raise ValueError(f"account {key} is blocked, unblock it first")
        name = conn.account_names[account_id]
        targets = self.explicit_targets(conn)
        if name not in targets and str(account_id) not in targets:
            targets.append(name)
        conn.config.target_accounts = targets
        await self.save(conn.config)
        return conn

    # stops sending orders to one account, the others stay as they are.
    # raises ValueError for an account that is not there
    async def remove_target(self, label, key):
        conn = self.live.get(label)
        if conn is None:
            return None
        account_id = conn.account_ids.get(key)
        if account_id is None and key not in conn.config.target_accounts:
            raise ValueError(f"account {key} not found on connection {label}")
        gone = {key}
        if account_id is not None:
            gone |= {conn.account_names[account_id], str(account_id)}
        targets = [t for t in self.explicit_targets(conn) if t not in gone]
        conn.config.target_accounts = targets
        if not targets:
            # an empty list means "every account known when it was chosen", so knowing none
            # keeps it at no account at all
            conn.config.known_accounts = []
        await self.save(conn.config)
        return conn

    # accounts that are never traded or flattened. they also leave the targets
    async def set_blocked(self, label, keys):
        conn = self.live.get(label)
        if conn is None:
            return None
        blocked = []
        for key in keys:
            # keep the name when we know the account, it reads better than an id
            account_id = conn.account_ids.get(key)
            name = conn.account_names[account_id] if account_id is not None else key
            if name not in blocked:
                blocked.append(name)
        gone = set(blocked) | {str(conn.account_ids[n]) for n in blocked if n in conn.account_ids}
        conn.config.blocked_accounts = blocked
        targets = [t for t in conn.config.target_accounts if t not in gone]
        if conn.config.target_accounts and not targets:
            # the only targets got blocked. an empty list would mean "every account", so it
            # becomes "no account" instead (see remove_target)
            conn.config.known_accounts = []
        conn.config.target_accounts = targets
        await self.save(conn.config)
        logger.info("[%s] blocked accounts: %s", label, ", ".join(blocked) or "none")
        return conn

    # the account's own size limits, like {"NQ": 1, "MNQ": 10}. empty removes them.
    # raises ValueError for an unknown account
    async def set_limits(self, label, key, limits):
        conn = self.live.get(label)
        if conn is None:
            return None
        account_id = conn.account_ids.get(key)
        if account_id is None:
            raise ValueError(f"account {key} not found on connection {label}")
        name = conn.account_names[account_id]
        account_limits = {k: v for k, v in conn.config.account_limits.items() if k not in (name, str(account_id))}
        if limits:
            account_limits[name] = limits
        conn.config.account_limits = account_limits
        await self.save(conn.config)
        return conn

    # reloads the account list now, so a new account shows up without waiting
    async def refresh(self, label):
        conn = self.live.get(label)
        if conn is None:
            return None
        await conn.refresh_accounts()
        return conn

    # try to log in again, for a connection that failed at startup
    async def reconnect(self, label):
        conn = self.live.get(label)
        if conn is None:
            return None
        await conn.start()
        return conn

    # ---------- reading ----------

    # the connections we should send orders to (all of them, or just one)
    def pick(self, label=None):
        if label:
            conn = self.live.get(label)
            return [conn] if conn else []
        return list(self.live.values())

    def health(self):
        return [conn.health() for conn in self.live.values()]

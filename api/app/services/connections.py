import asyncio
import json
import logging
from datetime import datetime, timezone

from app.models import ConnectionConfig
from app.tradovate.client import TradovateConnection, TradovateError

logger = logging.getLogger(__name__)


# keeps the list of tradovate logins: the live ones in memory, the saved ones in the database
class ConnectionManager:
    def __init__(self, db, crypto, settings):
        self.db = db
        self.crypto = crypto
        self.settings = settings
        # label -> TradovateConnection
        self.live = {}

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
        )

    async def save(self, config):
        api_secret_enc = self.crypto.encrypt(config.api_secret) if config.api_secret else None
        # upsert, so saving an existing connection keeps its place in the list
        await self.db.execute(
            "insert into connections "
            "(label, username, password_enc, demo, auth_mode, cid, api_secret_enc, device_id, target_accounts, created_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "on conflict(label) do update set "
            "username = excluded.username, password_enc = excluded.password_enc, demo = excluded.demo, "
            "auth_mode = excluded.auth_mode, cid = excluded.cid, api_secret_enc = excluded.api_secret_enc, "
            "device_id = excluded.device_id, target_accounts = excluded.target_accounts",
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
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )

    # ---------- start up / shut down ----------

    # log in to every saved connection at the same time
    async def load_saved(self):
        rows = await self.db.fetch_all("select * from connections order by rowid")
        for row in rows:
            config = self.config_from_row(row)
            self.live[config.label] = TradovateConnection(config, self.settings)
        await asyncio.gather(*(self.start_one(conn) for conn in self.live.values()))

    # a failed login does not stop the app, the connection stays in the list with its error
    async def start_one(self, conn):
        try:
            await conn.start()
        except TradovateError as exc:
            logger.error("[%s] could not start: %s", conn.label, exc)

    async def stop_all(self):
        await asyncio.gather(*(conn.stop() for conn in self.live.values()), return_exceptions=True)

    # ---------- changes made from the dashboard ----------

    # logs in first, only saves if the login worked
    async def add(self, config):
        if config.label in self.live:
            raise ValueError(f"a connection called '{config.label}' already exists")
        conn = TradovateConnection(config, self.settings)
        try:
            await conn.start()
        except TradovateError:
            await conn.stop()
            raise
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

    async def set_targets(self, label, targets):
        conn = self.live.get(label)
        if conn is None:
            return None
        conn.config.target_accounts = targets
        await self.save(conn.config)
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

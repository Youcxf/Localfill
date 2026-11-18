import hashlib
import secrets
import time
from datetime import datetime, timezone

from app.models import ApiToken, ApiTokenCreated

# every token starts with this, so one is easy to spot in a config file
PREFIX = "lf_"
# last_used_at is written at most this often per token, so a busy program does not write on every call
TOUCH_SECONDS = 60


def now_text():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


# api tokens let programs (like a trading bot) use the dashboard api without the password.
# only a sha256 of each token is stored, the token itself is shown once when it is made
class ApiTokens:
    def __init__(self, db):
        self.db = db
        # token id -> when we last wrote its last_used_at
        self.touched = {}

    async def create(self, name):
        token = PREFIX + secrets.token_urlsafe(32)
        created_at = now_text()
        token_id = await self.db.execute(
            "insert into api_tokens (name, token_hash, created_at) values (?, ?, ?)",
            (name, hash_token(token), created_at),
        )
        return ApiTokenCreated(id=token_id, name=name, created_at=created_at, token=token)

    async def list(self):
        rows = await self.db.fetch_all("select id, name, created_at, last_used_at from api_tokens order by id")
        return [ApiToken(**row) for row in rows]

    # true if there was one to delete
    async def revoke(self, token_id):
        row = await self.db.fetch_one("select id from api_tokens where id = ?", (token_id,))
        if row is None:
            return False
        await self.db.execute("delete from api_tokens where id = ?", (token_id,))
        self.touched.pop(token_id, None)
        return True

    # the token's id when it is valid, otherwise None
    async def check(self, token):
        if not token:
            return None
        wanted = hash_token(token)
        rows = await self.db.fetch_all("select id, token_hash from api_tokens")
        found = None
        # compare every one in constant time, so the answer time gives nothing away
        for row in rows:
            if secrets.compare_digest(row["token_hash"], wanted):
                found = row["id"]
        if found is not None:
            await self.touch(found)
        return found

    async def touch(self, token_id):
        now = time.monotonic()
        if now - self.touched.get(token_id, -TOUCH_SECONDS) < TOUCH_SECONDS:
            return
        self.touched[token_id] = now
        await self.db.execute("update api_tokens set last_used_at = ? where id = ?", (now_text(), token_id))

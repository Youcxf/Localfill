import aiosqlite

# each item is one migration, they run in order and only once
# to change the schema later, add a new item at the end (never edit old ones)
MIGRATIONS = [
    """
    create table settings (
        key text primary key,
        value text not null
    );

    create table connections (
        label text primary key,
        username text not null,
        password_enc text not null,
        demo integer not null,
        auth_mode text not null,
        cid text,
        api_secret_enc text,
        device_id text not null,
        target_accounts text not null,
        created_at text not null
    );

    create table signals (
        id integer primary key autoincrement,
        received_at text not null,
        source text not null,
        payload text not null,
        status text not null,
        message text,
        result text
    );
    """,
    # the accounts a connection had when its targets were last chosen. with "every account"
    # picked, an account that shows up later is not traded until you select it
    """
    alter table connections add column known_accounts text;
    """,
]


class Database:
    def __init__(self, path):
        self.path = path
        self.conn = None

    async def open(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("pragma journal_mode = wal")
        await self.migrate()

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def migrate(self):
        cursor = await self.conn.execute("pragma user_version")
        row = await cursor.fetchone()
        current = row[0]
        for number in range(current, len(MIGRATIONS)):
            await self.conn.executescript(MIGRATIONS[number])
            await self.conn.execute(f"pragma user_version = {number + 1}")
        await self.conn.commit()

    # returns a list of dicts
    async def fetch_all(self, sql, params=()):
        cursor = await self.conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # returns one dict or None
    async def fetch_one(self, sql, params=()):
        cursor = await self.conn.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    # runs an insert / update / delete and returns the new row id
    async def execute(self, sql, params=()):
        cursor = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cursor.lastrowid

    async def get_setting(self, key, default=None):
        row = await self.fetch_one("select value from settings where key = ?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key, value):
        await self.execute(
            "insert into settings (key, value) values (?, ?) "
            "on conflict(key) do update set value = excluded.value",
            (key, value),
        )

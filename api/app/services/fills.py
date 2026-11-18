import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from app.models import FillRecord
from app.tradovate.client import TradovateError

logger = logging.getLogger(__name__)

# in /orderStrategyLink the entry order has this label, the brackets have "0", "1"...
ENTRY_LABEL = "256"
# a fill of an order localfill does not know waits this long before it counts as placed outside
# localfill, so an order localfill has just sent has the time to be written down first
UNKNOWN_WAIT_SECONDS = 2.0
# tradovate's fees on one fill, added together
FEE_FIELDS = ("commission", "clearingFee", "exchangeFee", "nfaFee", "brokerageFee", "ipFee", "orderRoutingFee")
# a fill that has no fee yet is looked up again for this long
FEE_RETRY_SECONDS = 60
# fills older than this are not kept in memory to spot new ones (the table still has them)
REMEMBER_DAYS = 7


def now_text():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ago_text(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec="seconds")


# tradovate's {"year": 2026, "month": 11, "day": 17} -> "2026-11-17"
def day_text(day):
    if not isinstance(day, dict) or not day.get("year"):
        return None
    return f"{day['year']:04d}-{day['month']:02d}-{day['day']:02d}"


# every fee of one fill added up, None when tradovate gave none
def fee_of(row):
    parts = [row[name] for name in FEE_FIELDS if isinstance(row.get(name), (int, float))]
    return round(sum(parts), 2) if parts else None


def to_record(row):
    return FillRecord(**dict(row, imported=bool(row["imported"])))


# what each order localfill placed was for (entry, take profit, stop, flatten, plain),
# so its fills can say it later, also after a restart
class OrderRoles:
    def __init__(self, db):
        self.db = db

    # role "bracket" is a take profit or a stop, which one is looked up when it fills
    async def remember(self, connection, account_id, order_id, role, strategy_id=None):
        if order_id is None:
            return
        await self.db.execute(
            "insert or replace into orders (order_id, connection, account_id, strategy_id, role, created_at) "
            "values (?, ?, ?, ?, ?, ?)",
            (order_id, connection, account_id, strategy_id, role, now_text()),
        )

    async def get(self, order_id):
        return await self.db.fetch_one("select * from orders where order_id = ?", (order_id,))

    async def set_role(self, order_id, role):
        await self.db.execute("update orders set role = ? where order_id = ?", (role, order_id))


# asks tradovate for new fills every second or so and keeps every one in the fills table: the
# entries, and also the take profits and stops that fill later at tradovate, and the firm's
# liquidations. each new one is published as a "fill" event, followed by a "position" event
class FillWatcher:
    def __init__(self, db, manager, resolver, roles, brackets, risk, events, settings):
        self.db = db
        self.manager = manager
        self.resolver = resolver
        self.roles = roles
        self.brackets = brackets
        self.risk = risk
        self.events = events
        self.settings = settings
        self.task = None
        self.unknown_wait = UNKNOWN_WAIT_SECONDS
        # connection label -> fill ids already stored, or skipped (fills of blocked accounts)
        self.done = {}
        # fill id -> when it was first seen with an order localfill does not know
        self.waiting = {}
        # connection label -> when fills were last read without an error, and the last error
        self.last_poll = {}
        self.last_error = {}
        # connections whose tradovate host has no fee list
        self.no_fees = set()

    def start(self):
        if self.settings.fill_poll_seconds > 0 and self.task is None:
            self.task = asyncio.create_task(self.loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def loop(self):
        while True:
            for conn in [c for c in list(self.manager.live.values()) if c.online]:
                await self.check(conn)
            await asyncio.sleep(self.settings.fill_poll_seconds)

    # one round for one connection: new fills, and the risk state now and then
    async def check(self, conn):
        try:
            await self.poll(conn)
            self.last_poll[conn.label] = now_text()
            self.last_error.pop(conn.label, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error[conn.label] = str(exc)
            logger.warning("[%s] could not read fills: %s", conn.label, exc)
        if self.settings.risk_poll_seconds > 0:
            try:
                await self.risk.get(conn, self.settings.risk_poll_seconds)
            except Exception as exc:
                logger.warning("[%s] could not read the risk state: %s", conn.label, exc)

    # stores the new fills of one connection and returns them
    async def poll(self, conn):
        rows = await conn.request("GET", "/fill/list") or []
        if not isinstance(rows, list):
            return []
        done = await self.done_for(conn.label)
        new = sorted((r for r in rows if r.get("id") is not None and r["id"] not in done), key=lambda r: r["id"])

        # fills from earlier rounds that still have no fee
        missing = await self.db.fetch_all(
            "select seq, fill_id from fills where connection = ? and fee is null and imported = 0 and seen_at >= ?",
            (conn.label, ago_text(seconds=FEE_RETRY_SECONDS)),
        )
        fees = await self.fee_list(conn) if new or missing else {}
        for row in missing:
            if fees.get(row["fill_id"]) is not None:
                await self.db.execute("update fills set fee = ? where seq = ?", (fees[row["fill_id"]], row["seq"]))

        # the first look at a connection only takes note of what is already there
        first_time = await self.db.get_setting(f"fills_seen:{conn.label}") is None
        known, orders = await self.lookup_orders(conn, new) if new else ({}, {})
        stored = []
        for row in new:
            record = await self.store(conn, row, known, orders, fees, first_time)
            if record:
                stored.append((record, row))
        if first_time:
            await self.db.set_setting(f"fills_seen:{conn.label}", now_text())
        else:
            await self.announce(conn, stored)
        return [record for record, _ in stored]

    # fill ids of this connection that are already in the table
    async def done_for(self, label):
        if label not in self.done:
            rows = await self.db.fetch_all(
                "select fill_id from fills where connection = ? and seen_at >= ?", (label, ago_text(days=REMEMBER_DAYS))
            )
            self.done[label] = {row["fill_id"] for row in rows}
        return self.done[label]

    # the orders of these fills: the ones localfill wrote down, and tradovate's own copy of the others
    async def lookup_orders(self, conn, rows):
        known, unknown = {}, []
        for row in rows:
            order_id = row.get("orderId")
            if order_id is None or order_id in known or order_id in unknown:
                continue
            mine = await self.roles.get(order_id)
            if mine:
                known[order_id] = mine
            else:
                unknown.append(order_id)
        orders = {}
        if unknown:
            try:
                items = await conn.request("GET", f"/order/items?ids={','.join(str(i) for i in unknown)}") or []
                orders = {item.get("id"): item for item in items if isinstance(item, dict)}
            except TradovateError as exc:
                logger.warning("[%s] could not read orders %s: %s", conn.label, unknown, exc)
        return known, orders

    # saves one fill. None when it is skipped (blocked account) or has to wait for the next round
    async def store(self, conn, row, known, orders, fees, imported):
        fill_id = row["id"]
        order_id = row.get("orderId")
        mine = known.get(order_id)
        order = orders.get(order_id, {})
        account_id = row.get("accountId") or (mine or {}).get("account_id") or order.get("accountId")
        if account_id is None:
            # tried again next round, and given up on after a minute
            first_seen = self.waiting.setdefault(fill_id, time.monotonic())
            if time.monotonic() - first_seen > 60:
                logger.warning("[%s] fill %s: could not tell whose it is, skipped", conn.label, fill_id)
                self.done[conn.label].add(fill_id)
                self.waiting.pop(fill_id, None)
            return None
        # a blocked account's fills are its owner's business
        if conn.is_blocked(account_id):
            self.done[conn.label].add(fill_id)
            return None

        role, strategy_id = await self.role_of(conn, row, account_id, mine, order, imported)
        if role is None:
            return None
        self.waiting.pop(fill_id, None)

        contract_id = row.get("contractId")
        contract = await self.resolver.name_for_id(conn, contract_id) if contract_id is not None else "?"
        try:
            seq = await self.db.execute(
                "insert into fills (fill_id, connection, account_id, account_name, contract, action, qty, price, "
                "order_id, strategy_id, role, ts, trade_date, seen_at, imported, fee) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fill_id, conn.label, account_id, conn.account_names.get(account_id, str(account_id)), contract,
                    str(row.get("action", "")).lower(), row.get("qty", 0), row.get("price", 0), order_id, strategy_id,
                    role, row.get("timestamp"), day_text(row.get("tradeDate")), now_text(), int(imported), fees.get(fill_id),
                ),
            )
        except sqlite3.IntegrityError:
            # stored in an earlier round already
            self.done[conn.label].add(fill_id)
            return None
        self.done[conn.label].add(fill_id)
        return to_record(await self.db.fetch_one("select * from fills where seq = ?", (seq,)))

    # (role, strategy id) of a fill. (None, None) while an unknown order gets its time to be written down
    async def role_of(self, conn, row, account_id, mine, order, imported):
        order_id = row.get("orderId")
        if mine:
            role = mine["role"]
            if role == "bracket":
                role = await self.leg_role(conn, order_id)
                await self.roles.set_role(order_id, role)
            return role, mine["strategy_id"]

        # maybe a part of a bracket localfill started, that was not written down
        found = await self.in_strategies(conn, account_id, order_id)
        if found:
            return found

        if not imported:
            first_seen = self.waiting.setdefault(row["id"], time.monotonic())
            if time.monotonic() - first_seen < self.unknown_wait:
                return None, None
        # not ours: the firm's risk system (an admin order) or someone else, like you in tradovate
        role = "liquidation" if order.get("admin") else "other"
        if order_id is not None:
            await self.roles.remember(conn.label, account_id, order_id, role)
        return role, None

    # a bracket's take profit is a limit order, its stop a stop order
    async def leg_role(self, conn, order_id):
        versions = await conn.request("GET", f"/orderVersion/deps?masterid={order_id}") or []
        order_type = versions[-1].get("orderType") if versions else None
        return "take_profit" if order_type == "Limit" else "stop"

    async def in_strategies(self, conn, account_id, order_id):
        if order_id is None:
            return None
        for strategy_id in await self.brackets.on_account(conn.label, account_id):
            links = await conn.request("GET", f"/orderStrategyLink/deps?masterid={strategy_id}") or []
            for link in links:
                if link.get("orderId") != order_id:
                    continue
                role = "entry" if link.get("label") == ENTRY_LABEL else await self.leg_role(conn, order_id)
                await self.roles.remember(conn.label, account_id, order_id, role, strategy_id)
                return role, strategy_id
        return None

    # tradovate's fee per fill id. some hosts have no fee list, those are not asked again
    async def fee_list(self, conn):
        if conn.label in self.no_fees:
            return {}
        try:
            rows = await conn.request("GET", "/fillFee/list") or []
        except TradovateError as exc:
            if getattr(exc, "status", None) == 404:
                self.no_fees.add(conn.label)
            logger.warning("[%s] could not read the fill fees: %s", conn.label, exc)
            return {}
        fees = {}
        for row in rows if isinstance(rows, list) else []:
            fill_id = row.get("fillId", row.get("id"))
            if fill_id is not None:
                fees[fill_id] = fee_of(row)
        return fees

    # a "fill" event for each new fill, then a "position" event for each account and contract it touched
    async def announce(self, conn, stored):
        touched = {}
        liquidated = False
        for record, row in stored:
            self.events.publish("fill", record.model_dump())
            touched[(record.account_id, row.get("contractId"))] = record
            liquidated = liquidated or record.role == "liquidation"
        for (account_id, contract_id), record in touched.items():
            try:
                positions = await conn.request("GET", f"/position/list?accountId={account_id}") or []
            except TradovateError as exc:
                logger.warning("[%s] could not read the position after a fill: %s", conn.label, exc)
                continue
            net_pos = sum(
                p.get("netPos", 0) for p in positions
                if p.get("accountId", account_id) == account_id and p.get("contractId") == contract_id
            )
            self.events.publish("position", {
                "connection": conn.label,
                "account_id": account_id,
                "account_name": record.account_name,
                "contract": record.contract,
                "net_pos": net_pos,
            })
        # the firm closed something: its lock is probably on now
        if liquidated:
            await self.risk.refresh(conn)

    # ---------- reading ----------

    # fills with a seq above since, oldest first
    async def since(self, seq, limit):
        rows = await self.db.fetch_all("select * from fills where seq > ? order by seq limit ?", (seq, limit))
        return [to_record(row) for row in rows]

    # the fees of one account's fills on one trade date
    async def fees_on(self, connection, account_id, trade_date):
        if trade_date is None:
            return None
        row = await self.db.fetch_one(
            "select sum(fee) as total from fills where connection = ? and account_id = ? and trade_date = ?",
            (connection, account_id, trade_date),
        )
        total = row["total"] if row else None
        return round(total, 2) if total is not None else 0.0

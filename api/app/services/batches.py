import asyncio
import logging
import sqlite3

from app.models import BatchRecord
from app.services.dispatcher import product_for
from app.services.signals import HALTED, now_text

logger = logging.getLogger(__name__)


# a batch was checked and refused, nothing was sent
class BatchRefused(Exception):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class BatchHalted(Exception):
    pass


# the batch's status, from the statuses of its orders
def batch_status(records):
    statuses = [record.status for record in records]
    if statuses and all(s == "dry_run" for s in statuses):
        return "dry_run"
    if statuses and all(s == "done" for s in statuses):
        return "done"
    if any(s in ("done", "partial") for s in statuses):
        return "partial"
    return "failed"


# different orders on different accounts, sent all at the same time. everything is checked
# before anything is sent, and each order is saved as its own signal (with its own client_id)
class Batches:
    def __init__(self, db, processor, dispatcher, manager, brackets):
        self.db = db
        self.processor = processor
        self.dispatcher = dispatcher
        self.manager = manager
        self.brackets = brackets

    async def send(self, body, source):
        # sent before: the first answer, nothing is sent again
        if body.client_id:
            existing = await self.by_client_id(body.client_id)
            if existing:
                return existing
        if self.processor.halted and any(order.action != "flatten" for order in body.orders):
            raise BatchHalted(HALTED)
        problems = await self.check(body.orders)
        if problems:
            raise BatchRefused(problems)

        received_at = now_text()
        try:
            batch_id = await self.db.execute(
                "insert into batches (client_id, received_at, status) values (?, ?, 'pending')",
                (body.client_id, received_at),
            )
        except sqlite3.IntegrityError:
            # the same batch arrived twice at the same moment, the other one won
            return await self.by_client_id(body.client_id)

        # each order is its own signal. one whose client_id was seen before is not sent again
        records, to_run = [], []
        for order in body.orders:
            record, should_run = await self.processor.receive(order, source, batch_id)
            if should_run:
                to_run.append((len(records), record.id, order))
            records.append(record)

        # one turn of the queue for the whole batch, so no other signal slips in between,
        # and every order runs at the same time
        logger.info("batch %s: sending %d of %d order(s)", batch_id, len(to_run), len(records))
        async with self.processor.lock:
            finished = await asyncio.gather(*(self.processor.run_now(signal_id, order) for _, signal_id, order in to_run))
        for (index, _, _), record in zip(to_run, finished):
            records[index] = record

        status = batch_status(records)
        await self.db.execute("update batches set status = ? where id = ?", (status, batch_id))
        return BatchRecord(batch_id=batch_id, client_id=body.client_id, status=status, received_at=received_at, orders=records)

    # every reason to refuse the batch. empty when it can be sent
    async def check(self, orders):
        problems = []
        # (connection, account id) -> the order number that trades it
        owner = {}
        client_ids = {}
        limits = self.manager.settings.symbol_limits
        for number, order in enumerate(orders, start=1):
            if order.client_id:
                if order.client_id in client_ids:
                    problems.append(f"order {number}: client_id {order.client_id} is also order {client_ids[order.client_id]}")
                    continue
                client_ids[order.client_id] = number
                # already sent before: it will not be sent again, so there is nothing to check
                if await self.processor.by_client_id(order.client_id):
                    continue
            if not order.accounts:
                problems.append(f"order {number}: needs accounts")
                continue

            plan = None
            if order.uses_bracket:
                try:
                    plan = await self.brackets.plan_for(order)
                except ValueError as exc:
                    problems.append(f"order {number}: {exc}")
                    continue
            too_big = self.dispatcher.size_problem(order, plan)
            if too_big:
                problems.append(f"order {number}: {too_big}")

            # two orders on one account would race on its position
            conns = self.manager.pick(order.connection)
            product = product_for(order, limits)
            for key in order.accounts:
                found = [(conn, conn.account_ids[key]) for conn in conns if key in conn.account_ids]
                places = [(conn.label, account_id) for conn, account_id in found] or [("", key)]
                for place in places:
                    if owner.get(place, number) != number:
                        problems.append(f"order {number}: account {key} is also in order {owner[place]}")
                    owner[place] = number
                for conn, account_id in found:
                    too_big = self.dispatcher.account_size_problem(conn, account_id, order, plan, product)
                    if too_big:
                        problems.append(f"order {number}: {too_big}")
        return problems

    async def by_client_id(self, client_id):
        row = await self.db.fetch_one("select * from batches where client_id = ?", (client_id,))
        return await self.to_record(row) if row else None

    async def get(self, batch_id):
        row = await self.db.fetch_one("select * from batches where id = ?", (batch_id,))
        return await self.to_record(row) if row else None

    async def to_record(self, row):
        rows = await self.db.fetch_all("select * from signals where batch_id = ? order by id", (row["id"],))
        return BatchRecord(
            batch_id=row["id"],
            client_id=row["client_id"],
            status=row["status"],
            received_at=row["received_at"],
            orders=[self.processor.to_record(r) for r in rows],
        )

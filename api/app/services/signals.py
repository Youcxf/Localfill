import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from app.models import SIGNAL_STATUSES, SignalCounts, SignalRecord

logger = logging.getLogger(__name__)


def now_text():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# takes a signal from "received" to "done": saves it, runs it, saves the result
class SignalProcessor:
    def __init__(self, db, dispatcher, events, settings):
        self.db = db
        self.dispatcher = dispatcher
        self.events = events
        self.settings = settings
        # signals run one at a time in the order they arrived,
        # so a "sell" followed by a "buy" can never swap places
        self.lock = asyncio.Lock()
        # signal text -> when we saw it, used to drop duplicates
        self.seen = {}
        # (symbol, connection, accounts) -> sent_at of the newest alert we accepted
        self.newest = {}

    def is_duplicate(self, signal):
        seconds = self.settings.dedupe_seconds
        # a position signal sent twice does nothing the second time, so it is never dropped.
        # that way two identical alerts on purpose (scaling in) are not lost in position mode
        if seconds <= 0 or signal.position is not None:
            return False
        now = time.monotonic()
        # forget the old ones
        self.seen = {key: when for key, when in self.seen.items() if now - when < seconds}
        key = signal.model_dump_json()
        if key in self.seen:
            return True
        self.seen[key] = now
        return False

    # true if a newer alert for the same thing was already accepted (alerts can arrive out of order)
    def is_late(self, signal):
        if signal.sent_at is None:
            return False
        key = (signal.contract or signal.symbol, signal.connection, tuple(signal.accounts or ()))
        newest = self.newest.get(key)
        if newest is not None and signal.sent_at < newest:
            return True
        self.newest[key] = signal.sent_at
        return False

    # saves the signal. returns the record and whether it should be executed
    async def receive(self, signal, source):
        if source == "webhook" and self.is_duplicate(signal):
            record = await self.save(signal, source, "duplicate", "same signal came in a moment ago, ignored")
            return record, False
        if self.is_late(signal):
            message = "a newer alert for this symbol already arrived, this late one was ignored"
            record = await self.save(signal, source, "duplicate", message)
            return record, False
        record = await self.save(signal, source, "pending")
        logger.info("signal %s from %s: %s %s x%s", record.id, source, signal.action, signal.symbol, signal.qty)
        return record, True

    async def execute(self, signal_id, signal):
        async with self.lock:
            dry_run = await self.db.get_setting("dry_run", "0") == "1"
            if dry_run:
                return await self.finish(signal_id, "dry_run", "dry run is on, no order was sent")
            try:
                result = await self.dispatcher.run(signal)
            except Exception as exc:
                logger.exception("signal %s crashed", signal_id)
                return await self.finish(signal_id, "error", str(exc))

        if result.failed == 0 and result.succeeded > 0:
            status = "done"
        elif result.succeeded > 0:
            status = "partial"
        else:
            status = "failed"

        message = "; ".join(result.warnings)
        if not message and status != "done":
            errors = [r.error for r in result.results if r.error]
            message = errors[0] if errors else None
        return await self.finish(signal_id, status, message or None, result)

    # signals that were still running when localfill stopped (crash, power cut). we can not
    # know if their orders went out, so they are marked as errors instead of staying pending
    async def close_unfinished(self):
        rows = await self.db.fetch_all("select id from signals where status = 'pending'")
        for row in rows:
            await self.finish(row["id"], "error", "localfill stopped while this signal was running, check the accounts")
        if rows:
            logger.warning("%d signal(s) were still running at the last shutdown, marked as errors", len(rows))

    # ---------- database ----------

    async def save(self, signal, source, status, message=None):
        signal_id = await self.db.execute(
            "insert into signals (received_at, source, payload, status, message) values (?, ?, ?, ?, ?)",
            (now_text(), source, signal.model_dump_json(), status, message),
        )
        record = await self.get(signal_id)
        self.events.publish("signal", record.model_dump())
        return record

    async def finish(self, signal_id, status, message=None, result=None):
        result_text = result.model_dump_json() if result else None
        await self.db.execute(
            "update signals set status = ?, message = ?, result = ? where id = ?",
            (status, message, result_text, signal_id),
        )
        record = await self.get(signal_id)
        self.events.publish("signal", record.model_dump())
        return record

    async def get(self, signal_id):
        row = await self.db.fetch_one("select * from signals where id = ?", (signal_id,))
        return self.to_record(row) if row else None

    # newest first. status is optional, for the tabs in the dashboard
    async def list_recent(self, limit=50, offset=0, status=None):
        if status:
            sql = "select * from signals where status = ? order by id desc limit ? offset ?"
            rows = await self.db.fetch_all(sql, (status, limit, offset))
        else:
            rows = await self.db.fetch_all("select * from signals order by id desc limit ? offset ?", (limit, offset))
        return [self.to_record(row) for row in rows]

    # how many signals there are per status, and in total
    async def counts(self):
        rows = await self.db.fetch_all("select status, count(*) as n from signals group by status")
        found = {row["status"]: row["n"] for row in rows}
        per_status = {status: found.get(status, 0) for status in SIGNAL_STATUSES}
        return SignalCounts(total=sum(per_status.values()), **per_status)

    def to_record(self, row):
        return SignalRecord(
            id=row["id"],
            received_at=row["received_at"],
            source=row["source"],
            payload=json.loads(row["payload"]),
            status=row["status"],
            message=row["message"],
            result=json.loads(row["result"]) if row["result"] else None,
        )

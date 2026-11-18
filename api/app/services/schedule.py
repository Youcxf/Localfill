import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import Signal

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")


# the next monday to friday at the given new york time, after now (both in new york time).
# adding days keeps the wall clock time, so it stays right across a daylight saving change
def next_run(now, at):
    when = now.replace(hour=at.hour, minute=at.minute, second=at.second, microsecond=0)
    if when <= now:
        when += timedelta(days=1)
    while when.weekday() >= 5:
        when += timedelta(days=1)
    return when


# the backup for a bot that is down: flattens every target account once a day (LOCALFILL_DAILY_FLATTEN_ET).
# it is saved as a signal with source "schedule". flattening a flat account does nothing, so holidays are fine
class DailyFlatten:
    def __init__(self, processor, settings):
        self.processor = processor
        self.settings = settings
        self.task = None

    def start(self):
        if self.settings.daily_flatten_et and self.task is None:
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
        at = time.fromisoformat(self.settings.daily_flatten_et)
        while True:
            when = next_run(datetime.now(NEW_YORK), at)
            logger.info("daily flatten at %s new york time", when.strftime("%a %Y-%m-%d %H:%M:%S"))
            # sleep in short steps, so a computer that slept or a clock that moved does not miss it
            while True:
                left = (when.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
                if left <= 0:
                    break
                await asyncio.sleep(min(left, 30))
            try:
                await self.run()
            except Exception:
                logger.exception("the daily flatten failed")

    async def run(self):
        signal = Signal(action="flatten")
        record, _ = await self.processor.receive(signal, "schedule")
        return await self.processor.execute(record.id, signal)

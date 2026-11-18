import asyncio
import logging
import os
import secrets

from app.crypto import Crypto
from app.db import Database
from app.lock import DataDirLock
from app.services.batches import Batches
from app.services.brackets import Brackets
from app.services.connections import ConnectionManager
from app.services.dispatcher import Dispatcher
from app.services.events import EventBus
from app.services.fills import FillWatcher, OrderRoles
from app.services.reports import Reports
from app.services.risk import RiskWatch
from app.services.schedule import DailyFlatten
from app.services.signals import SignalProcessor
from app.services.tokens import ApiTokens
from app.tradovate.contracts import ContractResolver

logger = logging.getLogger(__name__)


def new_webhook_token():
    return secrets.token_urlsafe(24)


# everything the two servers share: database, connections, services
class AppContext:
    def __init__(self, settings):
        self.settings = settings
        self.events = EventBus()
        self.resolver = ContractResolver(settings.contract_cache_seconds)
        # session cookie -> when it expires (unix time)
        self.sessions = {}
        # signals that are being executed in the background
        self.tasks = set()
        self.webhook_token = ""

    async def start(self):
        os.makedirs(self.settings.data_dir, exist_ok=True)
        self.lock = DataDirLock(self.settings.data_dir)
        self.lock.acquire()
        self.db = Database(os.path.join(self.settings.data_dir, "localfill.db"))
        await self.db.open()
        self.crypto = Crypto(os.path.join(self.settings.data_dir, "secret.key"))

        self.tokens = ApiTokens(self.db)
        self.manager = ConnectionManager(self.db, self.crypto, self.settings, self.events)
        self.brackets = Brackets(self.db)
        self.roles = OrderRoles(self.db)
        self.risk = RiskWatch(self.events)
        self.dispatcher = Dispatcher(self.manager, self.resolver, self.brackets, self.roles, self.risk)
        self.fills = FillWatcher(
            self.db, self.manager, self.resolver, self.roles, self.brackets, self.risk, self.events, self.settings
        )
        self.reports = Reports(self.manager, self.resolver, self.risk, self.fills)
        self.processor = SignalProcessor(self.db, self.dispatcher, self.events, self.settings)
        self.batches = Batches(self.db, self.processor, self.dispatcher, self.manager, self.brackets)
        self.daily_flatten = DailyFlatten(self.processor, self.settings)

        # the webhook token is created once and then kept
        token = await self.db.get_setting("webhook_token")
        if not token:
            token = new_webhook_token()
            await self.db.set_setting("webhook_token", token)
        self.webhook_token = token

        await self.processor.load()
        await self.processor.close_unfinished()
        await self.manager.load_saved()
        self.manager.start_background()
        self.fills.start()
        self.daily_flatten.start()

    # runs a coroutine in the background (keeps a reference so it is not garbage collected)
    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def stop(self):
        # let the signals that are running finish their orders
        if self.tasks:
            logger.info("waiting for %d running signal(s)", len(self.tasks))
            await asyncio.wait(self.tasks, timeout=8)
        await self.daily_flatten.stop()
        await self.fills.stop()
        await self.manager.stop_all()
        await self.db.close()
        self.lock.release()

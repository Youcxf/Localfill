import asyncio

import httpx
import pytest
from aiohttp.test_utils import TestServer

from app.config import Settings
from app.context import AppContext
from app.devtools.fake_tradovate import FakeBroker, make_app
from app.webapp import create_admin_app, create_ingress_app

PASSWORD = "correct horse"


# a fake tradovate running on a random port
@pytest.fixture
async def broker_server():
    broker = FakeBroker()
    broker.fixed_price = 20000.0
    server = TestServer(make_app(broker))
    await server.start_server()
    yield broker, server
    await server.close()


def make_settings(tmp_path, server):
    return Settings(
        data_dir=str(tmp_path),
        tradovate_demo_url=f"http://127.0.0.1:{server.port}/v1",
        # keep the retries fast so failing tests do not take long
        max_retries=2,
        retry_backoff_base=0.01,
        http_timeout_seconds=5,
        dedupe_seconds=0,
        static_dir=str(tmp_path / "no-frontend"),
    )


@pytest.fixture
async def ctx(tmp_path, broker_server):
    broker, server = broker_server
    context = AppContext(make_settings(tmp_path, server))
    await context.start()
    context.broker = broker
    yield context
    await context.stop()


# the dashboard api, already logged in
@pytest.fixture
async def admin(ctx):
    transport = httpx.ASGITransport(app=create_admin_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/auth/setup", json={"password": PASSWORD})
        yield client


# the public webhook server
@pytest.fixture
async def ingress(ctx):
    transport = httpx.ASGITransport(app=create_ingress_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# a logged in connection called "A" with two accounts
@pytest.fixture
async def connected(admin):
    response = await admin.post("/api/connections", json={"label": "A", "username": "someone", "password": "secret123"})
    assert response.status_code == 201, response.text
    return admin


# waits until the signals running in the background are done
async def settle(ctx):
    while ctx.tasks:
        await asyncio.wait(set(ctx.tasks))

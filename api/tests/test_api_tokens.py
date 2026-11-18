import asyncio

import httpx

from app.context import AppContext
from app.webapp import create_admin_app

from .conftest import make_settings


def token_client(ctx, token):
    transport = httpx.ASGITransport(app=create_admin_app(ctx))
    return httpx.AsyncClient(transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def test_a_token_is_shown_once_and_stored_as_a_hash(ctx, admin):
    created = (await admin.post("/api/tokens", json={"name": "bot"})).json()

    assert created["token"].startswith("lf_")
    listed = (await admin.get("/api/tokens")).json()
    assert [t["name"] for t in listed] == ["bot"]
    assert "token" not in listed[0]
    rows = await ctx.db.fetch_all("select * from api_tokens")
    assert created["token"] not in str(rows)


async def test_a_token_works_on_positions_and_events(ctx, connected, bot):
    assert (await bot.get("/api/positions")).status_code == 200

    # the event stream stays open for a token (a refused one would answer right away)
    request = asyncio.create_task(bot.get("/api/events"))
    done, _ = await asyncio.wait({request}, timeout=0.5)
    assert not done
    assert len(ctx.events.listeners) == 1
    request.cancel()


async def test_a_token_still_works_after_a_restart(ctx, admin, tmp_path, broker_server):
    _, server = broker_server
    token = (await admin.post("/api/tokens", json={"name": "bot"})).json()["token"]

    ctx.lock.release()
    second = AppContext(make_settings(tmp_path, server))
    await second.start()
    try:
        async with token_client(second, token) as client:
            assert (await client.get("/api/status")).status_code == 200
    finally:
        await second.stop()


async def test_a_revoked_token_gets_401(ctx, admin):
    created = (await admin.post("/api/tokens", json={"name": "bot"})).json()
    async with token_client(ctx, created["token"]) as client:
        assert (await client.get("/api/status")).status_code == 200

        assert (await admin.delete(f"/api/tokens/{created['id']}")).status_code == 204

        assert (await client.get("/api/status")).status_code == 401
    assert (await admin.delete(f"/api/tokens/{created['id']}")).status_code == 404


async def test_a_wrong_token_gets_401(ctx, admin):
    async with token_client(ctx, "lf_not-a-real-one") as client:
        assert (await client.get("/api/positions")).status_code == 401


async def test_a_token_can_not_use_session_only_routes(connected, bot):
    refused = [
        await bot.put("/api/connections/A/blocked", json={"blocked_accounts": []}),
        await bot.put("/api/connections/A/limits", json={"account": "DEMO1001", "max_contracts": {"MNQ": 50}}),
        await bot.post("/api/tokens", json={"name": "another"}),
        await bot.get("/api/tokens"),
        await bot.patch("/api/settings", json={"dry_run": False}),
        await bot.post("/api/settings/webhook-token/regenerate"),
        await bot.post("/api/connections", json={"label": "B", "username": "x", "password": "y"}),
        await bot.delete("/api/connections/A"),
        await bot.post("/api/resume"),
    ]

    assert [r.status_code for r in refused] == [403] * len(refused)
    # reading is fine
    assert (await bot.get("/api/settings")).status_code == 200


async def test_the_last_use_of_a_token_is_written_down(ctx, admin, bot):
    await bot.get("/api/status")

    listed = (await admin.get("/api/tokens")).json()
    assert listed[0]["last_used_at"] is not None


async def test_signals_sent_with_a_token_say_api(connected, bot):
    record = (await bot.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})).json()

    assert record["source"] == "api"

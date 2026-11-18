import asyncio
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models import Signal

from .conftest import settle

# ---------- client_id ----------


def test_client_id_has_to_be_simple():
    Signal(action="buy", symbol="MNQ", client_id="run1-acct12-2026-11-17:entry.1")
    with pytest.raises(ValidationError):
        Signal(action="buy", symbol="MNQ", client_id="has spaces")
    with pytest.raises(ValidationError):
        Signal(action="buy", symbol="MNQ", client_id="x" * 101)


async def test_the_same_client_id_sends_one_order(ctx, connected):
    body = {"action": "buy", "symbol": "MNQ", "accounts": ["DEMO1001"], "client_id": "run1-entry"}

    first = (await connected.post("/api/signals", json=body)).json()
    second = (await connected.post("/api/signals", json=body)).json()

    assert first["id"] == second["id"]
    assert second["status"] == "done"
    assert second["client_id"] == "run1-entry"
    assert len(ctx.broker.order_requests) == 1


async def test_two_with_the_same_client_id_at_once_send_one_order(ctx, connected):
    body = {"action": "buy", "symbol": "MNQ", "accounts": ["DEMO1001"], "client_id": "run1-entry"}

    first, second = await asyncio.gather(connected.post("/api/signals", json=body), connected.post("/api/signals", json=body))

    assert first.json()["id"] == second.json()["id"]
    assert len(ctx.broker.order_requests) == 1
    rows = await ctx.db.fetch_all("select id from signals")
    assert len(rows) == 1


async def test_a_signal_can_be_found_by_its_client_id(connected):
    record = (await connected.post("/api/signals", json={"action": "flatten", "client_id": "close-1"})).json()

    found = await connected.get("/api/signals/client/close-1")

    assert found.json()["id"] == record["id"]
    assert (await connected.get("/api/signals/client/nope")).status_code == 404


async def test_a_client_id_is_never_run_twice_even_after_an_error(ctx, connected):
    ctx.broker.refuse_orders = True
    body = {"action": "buy", "symbol": "MNQ", "client_id": "try-1"}
    first = (await connected.post("/api/signals", json=body)).json()
    ctx.broker.refuse_orders = False

    second = (await connected.post("/api/signals", json=body)).json()

    assert first["id"] == second["id"]
    assert second["status"] == "failed"


async def test_the_webhook_takes_a_client_id_too(ctx, connected, ingress):
    body = {"action": "buy", "symbol": "MNQ", "client_id": "tv-1"}

    first = await ingress.post(f"/hook/{ctx.webhook_token}", json=body)
    await settle(ctx)
    second = await ingress.post(f"/hook/{ctx.webhook_token}", json=body)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json() == {"signal_id": first.json()["signal_id"], "status": "done"}
    assert len(ctx.broker.order_requests) == 2


async def test_a_client_id_skips_the_late_alert_check(ctx, connected):
    newer = datetime.fromisoformat("2026-11-17T10:00:05+00:00")
    older = datetime.fromisoformat("2026-11-17T10:00:00+00:00")
    await ctx.processor.receive(Signal(action="buy", symbol="MNQ", sent_at=newer), "dashboard")

    _, should_run = await ctx.processor.receive(Signal(action="buy", symbol="MNQ", sent_at=older, client_id="a"), "dashboard")

    assert should_run


# ---------- batches ----------

# five accounts, so a batch can give each one its own order
@pytest.fixture
async def five(ctx, admin):
    ctx.broker.extra_accounts.extend({"id": i, "name": f"DEMO{i}", "active": True} for i in (1003, 1004, 1005))
    response = await admin.post("/api/connections", json={"label": "A", "username": "someone", "password": "secret123"})
    assert response.status_code == 201
    return admin


def order(account, qty, points, stop, client_id=None):
    return {
        "client_id": client_id or f"run1-{account}-entry", "accounts": [account], "action": "buy", "symbol": "MNQ",
        "qty": qty, "take_profits": [{"qty": qty, "points": points}], "stop_loss": stop, "if_open": "skip",
    }


async def test_a_batch_gives_every_account_its_own_order_at_once(ctx, five):
    ctx.settings.max_contracts_per_symbol = "MNQ=48"
    sizes = {"DEMO1001": 16, "DEMO1002": 3, "DEMO1003": 48, "DEMO1004": 1, "DEMO1005": 7}
    orders = [order(name, qty, 10 + qty, 20 + qty) for name, qty in sizes.items()]

    response = await five.post("/api/batches", json={"client_id": "run1-2026-11-17-1000", "orders": orders})

    batch = response.json()
    assert response.status_code == 200, batch
    assert batch["status"] == "done"
    assert batch["client_id"] == "run1-2026-11-17-1000"
    # the answer keeps the order of the request
    assert [o["payload"]["accounts"] for o in batch["orders"]] == [[name] for name in sizes]
    assert all(o["source"] == "dashboard" and o["batch_id"] == batch["batch_id"] for o in batch["orders"])
    # each account got its own size and bracket
    sent = {r["accountId"]: r for r in ctx.broker.strategy_requests}
    assert {account_id: sent[account_id]["params"].count('"qty"') for account_id in sent} == {i: 1 for i in range(1001, 1006)}
    positions = {key[0]: p["netPos"] for key, p in ctx.broker.positions.items()}
    assert positions == {1001: 16, 1002: 3, 1003: 48, 1004: 1, 1005: 7}
    # every entry within a second of the others
    times = [datetime.fromisoformat(f["timestamp"].replace("Z", "+00:00")) for f in ctx.broker.fills]
    assert (max(times) - min(times)).total_seconds() < 1


async def test_a_batch_is_sent_together_not_one_by_one(ctx, five):
    # every bracket takes 0.4 s at the mock: one after the other would be 2 s
    ctx.broker.strategy_delay = 0.4
    orders = [order(f"DEMO{i}", 1, 10, 10) for i in range(1001, 1006)]

    started = asyncio.get_running_loop().time()
    batch = (await five.post("/api/batches", json={"orders": orders})).json()
    elapsed = asyncio.get_running_loop().time() - started

    assert batch["status"] == "done"
    assert elapsed < 1.2


async def test_one_bad_order_and_nothing_is_sent(ctx, five):
    orders = [order("DEMO1001", 2, 10, 10), order("DEMO1002", 100, 10, 10), order("DEMO1003", 1, 10, 10)]

    response = await five.post("/api/batches", json={"client_id": "b1", "orders": orders})

    assert response.status_code == 422
    assert response.json()["detail"] == ["order 2: asks for 100 contracts, the limit is 10 (LOCALFILL_MAX_CONTRACTS), nothing was sent"]
    assert ctx.broker.strategy_requests == []
    assert await ctx.db.fetch_all("select * from signals") == []
    # nothing was saved, so the fixed batch can use the same client_id
    orders[1]["qty"] = 1
    orders[1]["take_profits"][0]["qty"] = 1
    assert (await five.post("/api/batches", json={"client_id": "b1", "orders": orders})).json()["status"] == "done"


async def test_a_batch_checks_accounts_and_brackets(ctx, five):
    orders = [
        {"action": "buy", "symbol": "MNQ", "stop_loss": 10},
        order("DEMO1002", 1, 10, 10),
        order("DEMO1002", 1, 10, 10, client_id="other"),
        {"accounts": ["DEMO1003"], "action": "buy", "symbol": "MNQ", "preset": "nope"},
    ]

    response = await five.post("/api/batches", json={"orders": orders})

    assert response.json()["detail"] == [
        "order 1: needs accounts",
        "order 3: account DEMO1002 is also in order 2",
        "order 4: there is no preset called 'nope'",
    ]
    assert ctx.broker.strategy_requests == []


async def test_an_account_limit_refuses_the_whole_batch(ctx, five):
    await five.put("/api/connections/A/limits", json={"account": "DEMO1002", "max_contracts": {"MNQ": 1}})
    orders = [order("DEMO1001", 2, 10, 10), order("DEMO1002", 2, 10, 10)]

    response = await five.post("/api/batches", json={"orders": orders})

    assert response.status_code == 422
    assert response.json()["detail"] == ["order 2: account DEMO1002 is limited to 1 MNQ, this asks for 2. nothing sent"]
    assert ctx.broker.strategy_requests == []


async def test_sending_a_batch_again_sends_nothing_new(ctx, five):
    body = {"client_id": "run1-1000", "orders": [order("DEMO1001", 2, 10, 10), order("DEMO1002", 1, 10, 10)]}

    first = (await five.post("/api/batches", json=body)).json()
    second = (await five.post("/api/batches", json=body)).json()

    assert second["batch_id"] == first["batch_id"]
    assert [o["id"] for o in second["orders"]] == [o["id"] for o in first["orders"]]
    assert len(ctx.broker.strategy_requests) == 2
    assert (await five.get("/api/batches/client/run1-1000")).json()["batch_id"] == first["batch_id"]
    assert (await five.get(f"/api/batches/{first['batch_id']}")).json()["status"] == "done"


async def test_a_half_sent_batch_can_be_sent_again(ctx, five):
    # the first order went out before a crash, the batch is sent again under a new id
    await five.post("/api/signals", json=order("DEMO1001", 2, 10, 10))
    body = {"client_id": "run1-retry", "orders": [order("DEMO1001", 2, 10, 10), order("DEMO1002", 1, 10, 10)]}

    batch = (await five.post("/api/batches", json=body)).json()

    assert batch["status"] == "done"
    assert [r["accountId"] for r in ctx.broker.strategy_requests] == [1001, 1002]


async def test_a_batch_in_dry_run_sends_nothing(ctx, five):
    await five.patch("/api/settings", json={"dry_run": True})

    batch = (await five.post("/api/batches", json={"orders": [order("DEMO1001", 2, 10, 10), order("DEMO1002", 1, 10, 10)]})).json()

    assert batch["status"] == "dry_run"
    assert [o["status"] for o in batch["orders"]] == ["dry_run", "dry_run"]
    assert ctx.broker.strategy_requests == []


async def test_a_batch_with_a_token_says_api(ctx, five, bot):
    batch = (await bot.post("/api/batches", json={"orders": [order("DEMO1001", 1, 10, 10)]})).json()

    assert batch["orders"][0]["source"] == "api"


async def test_a_locked_account_in_a_batch_only_fails_its_own_order(ctx, five):
    ctx.broker.lock_account(1002)
    await ctx.risk.refresh(ctx.manager.live["A"])

    batch = (await five.post("/api/batches", json={"orders": [order("DEMO1001", 1, 10, 10), order("DEMO1002", 1, 10, 10)]})).json()

    assert batch["status"] == "partial"
    assert [o["status"] for o in batch["orders"]] == ["done", "failed"]

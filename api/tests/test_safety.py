import asyncio
from datetime import datetime, time

import pytest

from app.config import Settings, parse_limits
from app.context import AppContext
from app.models import Signal
from app.services.schedule import NEW_YORK, next_run

from .conftest import make_settings

BRACKET = {"action": "buy", "symbol": "MNQ", "qty": 1, "take_profits": [{"qty": 1, "points": 10}], "stop_loss": 8}


def open_positions(broker):
    return {key: p["netPos"] for key, p in broker.positions.items() if p["netPos"] != 0}


def working(broker):
    return [o for o in broker.orders.values() if o["ordStatus"] in ("Working", "Suspended")]


# ---------- size limits per symbol ----------

def test_symbol_limits_are_read_from_text():
    assert parse_limits("MNQ=48, nq = 3,") == {"MNQ": 48, "NQ": 3}
    assert parse_limits("") == {}
    with pytest.raises(ValueError):
        Settings(max_contracts_per_symbol="MNQ:48")


async def test_each_symbol_has_its_own_limit(ctx, connected):
    ctx.settings.max_contracts_per_symbol = "MNQ=48,NQ=3"

    micro = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=48, accounts=["DEMO1001"]))
    mini = await ctx.dispatcher.run(Signal(action="buy", symbol="NQ", qty=4))
    other = await ctx.dispatcher.run(Signal(action="buy", symbol="ES", qty=11))

    assert micro.succeeded == 1
    assert mini.warnings[0] == "asks for 4 NQ, the limit is 3 (LOCALFILL_MAX_CONTRACTS_PER_SYMBOL), nothing was sent"
    # a product that is not listed uses LOCALFILL_MAX_CONTRACTS
    assert other.warnings[0] == "asks for 11 contracts, the limit is 10 (LOCALFILL_MAX_CONTRACTS), nothing was sent"
    assert [o["orderQty"] for o in ctx.broker.order_requests] == [48]


async def test_the_symbol_limit_also_reads_full_contract_names(ctx, connected):
    ctx.settings.max_contracts_per_symbol = "MNQ=2"

    by_contract = await ctx.dispatcher.run(Signal(action="buy", contract="MNQZ6", symbol="MNQ", qty=3))
    by_symbol = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQH7", qty=3))

    assert by_contract.total == 0 and by_symbol.total == 0
    assert ctx.broker.order_requests == []


async def test_settings_show_both_limits(ctx, admin):
    ctx.settings.max_contracts_per_symbol = "MNQ=48,NQ=3"

    settings = (await admin.get("/api/settings")).json()

    assert settings["max_contracts"] == 10
    assert settings["max_contracts_per_symbol"] == {"MNQ": 48, "NQ": 3}


# ---------- brackets and last month's contract ----------

async def test_a_bracket_is_refused_on_an_account_still_in_another_month(ctx, connected):
    ctx.broker.place_outside(1001, "MNQH7", "Buy", 3)

    for if_open in ("skip", "reverse", "replace"):
        result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", stop_loss=10, if_open=if_open, accounts=["DEMO1001"]))
        assert result.results[0].error == "still holds MNQH7 (3), roll it or flatten it first. nothing sent"
    assert ctx.broker.strategy_requests == []
    # the other account is fine
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", stop_loss=10, accounts=["DEMO1002"]))
    assert result.succeeded == 1


# ---------- flatten with a bracket working ----------

async def test_flatten_with_a_bracket_working_is_not_a_failure(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})
    await connected.post("/api/signals", json=BRACKET)

    record = (await connected.post("/api/flatten", json={})).json()

    row = record["result"]["results"][0]
    assert record["status"] == "done", row
    assert row["status"] == "closed 1 position(s), cancelled 2 order(s)"
    assert row["net_pos"] == 0
    assert open_positions(ctx.broker) == {}
    assert working(ctx.broker) == []


async def test_a_cancel_that_really_fails_is_still_an_error(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "order_type": "limit", "price": 19000})
    ctx.broker.refuse_cancels = True

    record = (await connected.post("/api/flatten", json={})).json()

    row = record["result"]["results"][0]
    assert row["success"] is False
    assert "cancel refused" in row["error"]


async def test_flatten_says_when_the_position_is_still_open(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "qty": 2})
    # tradovate keeps showing the old position for longer than localfill waits
    ctx.broker.stale_reads_after_fill = 100

    result = await ctx.dispatcher.run(Signal(action="flatten"))

    assert result.results[0].success is False
    assert result.results[0].error == "sent the close but tradovate still shows 2 open"
    assert result.results[0].net_pos == 2


# ---------- halt ----------

async def test_halt_flattens_and_refuses_new_orders(ctx, connected, bot):
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "qty": 2})

    answer = (await bot.post("/api/halt")).json()

    assert answer["halted"] is True
    assert answer["flatten"]["status"] == "done"
    assert open_positions(ctx.broker) == {}
    refused = (await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "client_id": "x1"})).json()
    assert refused["status"] == "error"
    assert "halted" in refused["message"]
    # it was never tried, so the same client_id still works after resume
    assert refused["client_id"] is None
    batch = await bot.post("/api/batches", json={"orders": [{"action": "buy", "symbol": "MNQ", "accounts": ["DEMO1001"]}]})
    assert batch.status_code == 409
    # flattening is still allowed
    assert (await connected.post("/api/flatten", json={})).json()["status"] == "done"
    assert (await connected.get("/api/status")).json()["halted"] is True

    # only the dashboard can resume
    assert (await bot.post("/api/resume")).status_code == 403
    assert (await connected.post("/api/resume")).json()["halted"] is False
    again = (await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "client_id": "x1"})).json()
    assert again["status"] == "done"


async def test_halt_does_not_wait_for_the_queue(ctx, connected):
    ctx.broker.order_delay = 1.5
    running = asyncio.create_task(connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"}))
    await asyncio.sleep(0.2)
    queued = asyncio.create_task(connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"}))
    await asyncio.sleep(0.1)

    started = asyncio.get_running_loop().time()
    await connected.post("/api/halt")
    assert asyncio.get_running_loop().time() - started < 1.0

    # the queued one ends as halted, the running one opened something: it gets flattened again after
    assert (await queued).json()["status"] == "error"
    assert (await running).json()["status"] == "done"
    while ctx.tasks:
        await asyncio.wait(set(ctx.tasks))
    assert open_positions(ctx.broker) == {}
    flattens = [s for s in (await connected.get("/api/signals")).json() if s["payload"]["action"] == "flatten"]
    assert len(flattens) == 2


async def test_halt_survives_a_restart(ctx, connected, tmp_path, broker_server):
    _, server = broker_server
    await connected.post("/api/halt")

    ctx.lock.release()
    second = AppContext(make_settings(tmp_path, server))
    await second.start()
    try:
        assert second.processor.halted is True
    finally:
        await second.stop()


# ---------- daily safety flatten ----------

def test_the_daily_flatten_skips_the_weekend():
    at = time(16, 44, 30)
    friday_morning = datetime(2026, 11, 20, 9, 0, tzinfo=NEW_YORK)
    friday_evening = datetime(2026, 11, 20, 17, 0, tzinfo=NEW_YORK)

    assert next_run(friday_morning, at) == datetime(2026, 11, 20, 16, 44, 30, tzinfo=NEW_YORK)
    assert next_run(friday_evening, at) == datetime(2026, 11, 23, 16, 44, 30, tzinfo=NEW_YORK)


def test_the_daily_flatten_keeps_new_york_time_across_daylight_saving():
    # daylight saving ends on sunday 2026-11-01
    friday = datetime(2026, 10, 30, 17, 0, tzinfo=NEW_YORK)

    monday = next_run(friday, time(16, 44, 30))

    assert (monday.hour, monday.minute) == (16, 44)
    assert monday.utcoffset().total_seconds() == -5 * 3600


async def test_the_daily_flatten_is_saved_as_a_schedule_signal(ctx, connected):
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})

    record = await ctx.daily_flatten.run()

    assert record.source == "schedule"
    assert record.status == "done"
    assert open_positions(ctx.broker) == {}


def test_a_bad_daily_flatten_time_is_refused():
    with pytest.raises(ValueError):
        Settings(daily_flatten_et="quarter to five")
    assert Settings(daily_flatten_et=" 16:44:30 ").daily_flatten_et == "16:44:30"


# ---------- rollover help ----------

async def test_the_front_month_of_a_symbol(connected):
    front = (await connected.get("/api/contracts/front?symbol=MNQ1!")).json()

    assert front == {"symbol": "MNQ", "name": "MNQZ6", "id": 1}
    assert (await connected.get("/api/contracts/front?symbol=XYZ")).status_code == 404


async def test_the_front_month_needs_a_connection(admin):
    assert (await admin.get("/api/contracts/front?symbol=MNQ")).status_code == 503

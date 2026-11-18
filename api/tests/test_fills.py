import asyncio

from app.context import AppContext
from app.models import Signal

from .conftest import make_settings

BRACKET = {"action": "buy", "symbol": "MNQ", "qty": 1, "take_profits": [{"qty": 1, "points": 10}], "stop_loss": 8}


def fill_events(queue):
    found = []
    while not queue.empty():
        event = queue.get_nowait()
        if event["type"] in ("fill", "position"):
            found.append(event)
    return found


# waits for the next event of this type, or fails after timeout seconds
async def next_event(queue, kind, timeout=2.0):
    async def wait():
        while True:
            event = await queue.get()
            if event["type"] == kind:
                return event["data"]

    return await asyncio.wait_for(wait(), timeout)


# the watcher's first look only takes note of what is already there
async def first_look(ctx):
    for conn in ctx.manager.pick():
        await ctx.fills.poll(conn)


async def poll(ctx):
    found = []
    for conn in ctx.manager.pick():
        found += await ctx.fills.poll(conn)
    return found


async def test_bracket_fills_come_back_as_events_with_their_role(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})
    await first_look(ctx)
    ctx.settings.fill_poll_seconds = 0.05
    ctx.fills.start()
    queue = ctx.events.subscribe()

    await connected.post("/api/signals", json=BRACKET)
    entry = await next_event(queue, "fill")
    position = await next_event(queue, "position")

    assert entry["role"] == "entry"
    assert entry["action"] == "buy"
    assert entry["contract"] == "MNQZ6"
    assert entry["account_name"] == "DEMO1001"
    assert entry["strategy_id"] is not None
    assert position == {"connection": "A", "account_id": 1001, "account_name": "DEMO1001", "contract": "MNQZ6", "net_pos": 1}

    # the take profit fills later, at tradovate
    ctx.broker.move_to(20010.0)
    take_profit = await next_event(queue, "fill")
    position = await next_event(queue, "position")

    assert take_profit["role"] == "take_profit"
    assert take_profit["action"] == "sell"
    assert take_profit["price"] == 20010.0
    assert take_profit["strategy_id"] == entry["strategy_id"]
    assert take_profit["seq"] > entry["seq"]
    assert position["net_pos"] == 0


async def test_a_stop_that_fills_while_localfill_is_off_shows_up_after(ctx, connected, tmp_path, broker_server):
    _, server = broker_server
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})
    await first_look(ctx)
    await connected.post("/api/signals", json=BRACKET)
    seen = await poll(ctx)
    assert [f.role for f in seen] == ["entry"]
    last_seq = seen[-1].seq

    # localfill stops, the stop fills, localfill starts again
    ctx.broker.move_to(19990.0)
    ctx.lock.release()
    second = AppContext(make_settings(tmp_path, server))
    await second.start()
    try:
        await poll(second)
        fills = await second.fills.since(last_seq, 500)
    finally:
        await second.stop()

    # the mock fills at the price it jumped to
    assert [(f.role, f.price, f.imported) for f in fills] == [("stop", 19990.0, False)]


async def test_fills_history_reads_on_from_a_seq(ctx, connected):
    await first_look(ctx)
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})
    await connected.post("/api/flatten", json={})
    await poll(ctx)

    every = (await connected.get("/api/fills")).json()
    later = (await connected.get(f"/api/fills?since={every[1]['seq']}")).json()

    assert [f["role"] for f in every] == ["plain", "plain", "flatten", "flatten"]
    assert [f["seq"] for f in later] == [f["seq"] for f in every[2:]]
    assert (await connected.get("/api/fills?limit=1")).json()[0]["seq"] == every[0]["seq"]


async def test_an_order_placed_outside_localfill_is_other(ctx, connected):
    await first_look(ctx)
    ctx.fills.unknown_wait = 0
    ctx.broker.place_outside(1002, "MNQZ6", "Buy", 1)

    fills = await poll(ctx)

    assert [(f.role, f.account_name) for f in fills] == [("other", "DEMO1002")]


async def test_an_admin_order_is_a_liquidation(ctx, connected):
    await first_look(ctx)
    ctx.fills.unknown_wait = 0
    queue = ctx.events.subscribe()
    ctx.broker.place_outside(1001, "MNQZ6", "Buy", 1)
    await poll(ctx)
    # the firm closes it and locks the account
    ctx.broker.lock_account(1001)
    ctx.broker.place_outside(1001, "MNQZ6", "Sell", 1, admin=True)

    fills = await poll(ctx)

    assert [f.role for f in fills] == ["liquidation"]
    # after a liquidation the risk state is read again: the account is locked now
    assert ctx.risk.known(ctx.manager.live["A"], 1001).locked is True
    assert "fill" in [e["type"] for e in fill_events(queue)]


async def test_an_unknown_order_waits_a_moment_before_it_counts_as_other(ctx, connected):
    await first_look(ctx)
    ctx.fills.unknown_wait = 60
    order_id = ctx.broker.place_outside(1001, "MNQZ6", "Buy", 1)

    # localfill writes it down a moment later (it was its own order after all)
    assert await poll(ctx) == []
    await ctx.roles.remember("A", 1001, order_id, "plain")
    fills = await poll(ctx)

    assert [f.role for f in fills] == ["plain"]


async def test_the_first_look_only_imports(ctx, connected):
    ctx.broker.place_outside(1001, "MNQZ6", "Buy", 1)
    queue = ctx.events.subscribe()

    fills = await poll(ctx)

    assert [(f.imported, f.role) for f in fills] == [(True, "other")]
    assert fill_events(queue) == []
    # the next fill is new
    ctx.fills.unknown_wait = 0
    ctx.broker.place_outside(1001, "MNQZ6", "Sell", 1)
    assert [f.imported for f in await poll(ctx)] == [False]


async def test_fills_of_a_blocked_account_are_not_kept(ctx, connected):
    await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["DEMO1002"]})
    await first_look(ctx)
    ctx.fills.unknown_wait = 0
    ctx.broker.place_outside(1002, "MNQZ6", "Buy", 1)

    assert await poll(ctx) == []
    assert (await connected.get("/api/fills")).json() == []


async def test_a_fill_is_never_stored_twice(ctx, connected):
    await first_look(ctx)
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})

    await poll(ctx)
    ctx.fills.done.clear()
    await poll(ctx)

    assert len((await connected.get("/api/fills")).json()) == 2


async def test_the_status_shows_the_last_fill_poll(ctx, connected):
    await first_look(ctx)
    await ctx.fills.check(ctx.manager.live["A"])

    status = (await connected.get("/api/status")).json()

    assert status["connections"][0]["label"] == "A"
    assert status["connections"][0]["online"] is True
    assert status["connections"][0]["last_fill_poll"] is not None


# ---------- fees ----------

async def test_a_round_trip_shows_its_fees(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})
    await first_look(ctx)
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "qty": 2})
    await connected.post("/api/flatten", json={})
    await poll(ctx)

    fills = (await connected.get("/api/fills")).json()
    accounts = (await connected.get("/api/accounts")).json()

    # 0.25 commission and 0.37 exchange fee per micro contract, 2 contracts per fill
    assert [f["fee"] for f in fills] == [1.24, 1.24]
    assert accounts[0]["fees_today"] == 2.48


async def test_a_fill_without_its_fee_gets_it_later(ctx, connected):
    await first_look(ctx)
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "accounts": ["DEMO1001"]})
    fee_rows = dict(ctx.broker.fill_fees)
    ctx.broker.fill_fees.clear()
    await poll(ctx)
    assert [f["fee"] for f in (await connected.get("/api/fills")).json()] == [None]

    ctx.broker.fill_fees.update(fee_rows)
    await poll(ctx)

    assert [f["fee"] for f in (await connected.get("/api/fills")).json()] == [0.62]


async def test_plain_and_bracket_orders_are_written_down(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", accounts=["DEMO1001"]))
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", accounts=["DEMO1002"], stop_loss=10))

    rows = await ctx.db.fetch_all("select role from orders order by order_id")

    assert [r["role"] for r in rows] == ["plain", "entry", "bracket"]

import asyncio

# the whole day of a bot driving localfill with an api token, at the mock
# (the "how the bot will use it" list of the feature notes)


def order(account, qty, points, stop, run="run1"):
    return {
        "client_id": f"{run}-{account}-2026-11-17-entry", "accounts": [account], "action": "buy", "symbol": "MNQ",
        "qty": qty, "take_profits": [{"qty": qty, "points": points}], "stop_loss": stop, "if_open": "skip",
    }


async def next_of(queue, kind, timeout=3.0):
    async def wait():
        while True:
            event = await queue.get()
            if event["type"] == kind:
                return event["data"]

    return await asyncio.wait_for(wait(), timeout)


async def test_a_bot_day_at_the_mock(ctx, admin, bot):
    broker = ctx.broker
    # one login with the bot's two accounts and your own one, which is blocked first
    broker.extra_accounts.append({"id": 1003, "name": "PERSONAL", "active": True})
    await admin.post("/api/connections", json={"label": "A", "username": "someone", "password": "secret123"})
    await admin.put("/api/connections/A/blocked", json={"blocked_accounts": ["PERSONAL"]})
    await admin.patch("/api/connections/A", json={"target_accounts": ["DEMO1001", "DEMO1002"]})
    broker.place_outside(1003, "MNQZ6", "Buy", 1)

    # 1. startup: status, accounts, positions, fills
    for conn in ctx.manager.pick():
        await ctx.fills.poll(conn)
    status = (await bot.get("/api/status")).json()
    assert (status["dry_run"], status["halted"], status["connections_online"]) == (False, False, 1)
    assert [a["name"] for a in (await bot.get("/api/accounts")).json()] == ["DEMO1001", "DEMO1002"]
    assert (await bot.get("/api/positions")).json()["positions"] == []
    assert (await bot.get("/api/fills?since=0")).json() == []

    # 2. live updates
    ctx.settings.fill_poll_seconds = 0.05
    ctx.fills.start()
    queue = ctx.events.subscribe()

    # 3. entry: one batch, one order per account
    batch = {"client_id": "run1-2026-11-17-1000", "orders": [order("DEMO1001", 4, 10, 8), order("DEMO1002", 2, 20, 8)]}
    sent = (await bot.post("/api/batches", json=batch)).json()
    assert sent["status"] == "done"
    entries = [await next_of(queue, "fill"), await next_of(queue, "fill")]
    assert sorted((f["account_name"], f["role"], f["qty"]) for f in entries) == [("DEMO1001", "entry", 4), ("DEMO1002", "entry", 2)]

    # 4. during the session the first take profit fills at tradovate
    broker.move_to(20010.0)
    take_profit = await next_of(queue, "fill")
    assert (take_profit["account_name"], take_profit["role"]) == ("DEMO1001", "take_profit")

    # 8. crash test: the batch is sent again, nothing new goes out
    again = (await bot.post("/api/batches", json=batch)).json()
    assert again["batch_id"] == sent["batch_id"]
    assert len(broker.strategy_requests) == 2

    # 5. before 16:45: a flatten with its accounts and a client_id
    flatten = {"action": "flatten", "accounts": ["DEMO1001", "DEMO1002"], "client_id": "run1-2026-11-17-flatten"}
    closed = (await bot.post("/api/signals", json=flatten)).json()
    assert closed["status"] == "done"
    assert [r["net_pos"] for r in closed["result"]["results"]] == [0, 0]

    # 6. evening: the real balances, your own account never in them
    accounts = {a["name"]: a for a in (await bot.get("/api/accounts")).json()}
    # 4 contracts out at the take profit, +10 points at 2 dollars
    assert accounts["DEMO1001"]["realized_pnl_today"] == 80.0
    # 2 contracts flattened at +10 points
    assert accounts["DEMO1002"]["realized_pnl_today"] == 40.0
    assert all(a["positions"] == [] for a in accounts.values())
    assert {(k[0], p["netPos"]) for k, p in broker.positions.items() if p["netPos"]} == {(1003, 1)}

    # 7. a new account was bought: it shows up, and is only traded once linked
    broker.extra_accounts.append({"id": 1004, "name": "FUNDED1", "active": True})
    refreshed = (await bot.post("/api/connections/A/refresh")).json()
    assert [a["name"] for a in refreshed["accounts"] if a["is_new"]] == ["FUNDED1"]
    assert (await next_of(queue, "accounts"))["change"] == "new"
    linked = (await bot.post("/api/connections/A/targets/add", json={"account": "FUNDED1"})).json()
    assert [a["name"] for a in linked["accounts"] if a["is_target"]] == ["DEMO1001", "DEMO1002", "FUNDED1"]

    # every fill is in the history, in order, and none is your own
    history = (await bot.get("/api/fills")).json()
    assert [f["seq"] for f in history] == sorted(f["seq"] for f in history)
    assert "PERSONAL" not in {f["account_name"] for f in history}

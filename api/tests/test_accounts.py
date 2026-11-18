from app.models import Signal

# ---------- helpers ----------


def open_positions(broker):
    return {key: p["netPos"] for key, p in broker.positions.items() if p["netPos"] != 0}


def accounts_by_name(health):
    return {a["name"]: a for a in health["accounts"]}


# two more accounts on the login, loaded into connection A
async def add_accounts(ctx, *accounts):
    ctx.broker.extra_accounts.extend(accounts)
    await ctx.manager.refresh("A")


def events_of(queue, kind):
    found = []
    while not queue.empty():
        event = queue.get_nowait()
        if event["type"] == kind:
            found.append(event["data"])
    return found


# ---------- blocked accounts ----------

async def test_a_blocked_account_listed_as_a_target_gets_nothing(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001", "DEMO1002"]})
    await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["DEMO1002"]})
    # even if the targets still name it (saved before it was blocked)
    ctx.manager.live["A"].config.target_accounts = ["DEMO1001", "DEMO1002"]

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))

    assert [r.account_name for r in result.results] == ["DEMO1001"]
    assert [o["accountId"] for o in ctx.broker.order_requests] == [1001]


async def test_blocking_leaves_the_targets(connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001", "DEMO1002"]})

    health = (await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["DEMO1002"]})).json()

    accounts = accounts_by_name(health)
    assert accounts["DEMO1002"]["blocked"] is True
    assert accounts["DEMO1002"]["is_target"] is False
    assert accounts["DEMO1001"]["is_target"] is True


async def test_empty_targets_still_skip_a_blocked_account(ctx, connected):
    await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["1002"]})

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))

    assert [r.account_id for r in result.results] == [1001]


async def test_a_blocked_account_named_in_an_alert_gets_an_error(ctx, connected):
    await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["DEMO1002"]})

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", accounts=["DEMO1002"]))

    assert result.succeeded == 0
    assert result.results[0].error == "account DEMO1002 is blocked"
    assert ctx.broker.order_requests == []


async def test_flatten_all_leaves_a_blocked_account_alone(ctx, connected):
    ctx.broker.place_outside(1002, "MNQZ6", "Buy", 3)
    await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["DEMO1002"]})

    await connected.post("/api/flatten", json={})

    assert open_positions(ctx.broker) == {(1002, 1): 3}


async def test_a_blocked_account_can_not_be_made_a_target(connected):
    await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["DEMO1002"]})

    patch = await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1002"]})
    add = await connected.post("/api/connections/A/targets/add", json={"account": "DEMO1002"})

    assert patch.status_code == 400
    assert add.status_code == 400
    assert "blocked" in add.json()["detail"]


async def test_blocking_the_only_target_does_not_turn_into_every_account(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})

    await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["DEMO1001"]})

    assert ctx.manager.live["A"].target_account_ids() == []


async def test_blocked_accounts_come_back_after_a_restart(ctx, connected):
    await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["DEMO1002"]})

    row = await ctx.db.fetch_one("select * from connections where label = 'A'")
    config = ctx.manager.config_from_row(row)

    assert config.blocked_accounts == ["DEMO1002"]


# ---------- named accounts that can not trade ----------

async def test_every_named_account_gets_a_result(ctx, connected):
    await add_accounts(ctx, {"id": 1003, "name": "DEMO1003", "active": True}, {"id": 1004, "name": "DEMO1004", "active": True})
    ctx.broker.closed_accounts.add("DEMO1003")
    await ctx.manager.refresh("A")
    await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["DEMO1004"]})

    signal = Signal(action="buy", symbol="MNQ", accounts=["DEMO1001", "NOPE", "DEMO1003", "DEMO1004"])
    result = await ctx.dispatcher.run(signal)

    assert result.total == 4
    assert result.succeeded == 1
    errors = sorted(r.error for r in result.results if r.error)
    assert errors == [
        "account DEMO1003 is closed or inactive",
        "account DEMO1004 is blocked",
        "account NOPE not found on connection A",
    ]
    assert [o["accountId"] for o in ctx.broker.order_requests] == [1001]


async def test_a_flatten_naming_an_unknown_account_says_so(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="flatten", accounts=["NOPE"]))

    assert result.results[0].error == "account NOPE not found on connection A"


# ---------- new accounts ----------

async def test_a_new_account_shows_up_but_gets_no_orders(ctx, connected):
    queue = ctx.events.subscribe()
    ctx.broker.extra_accounts.append({"id": 1003, "name": "DEMO1003", "active": True})

    health = (await connected.post("/api/connections/A/refresh")).json()

    assert events_of(queue, "accounts") == [
        {"connection": "A", "account_id": 1003, "name": "DEMO1003", "active": True, "change": "new"}
    ]
    assert accounts_by_name(health)["DEMO1003"]["is_new"] is True
    assert accounts_by_name(health)["DEMO1001"]["is_new"] is False
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))
    assert 1003 not in [o["accountId"] for o in ctx.broker.order_requests]


async def test_adding_one_target_keeps_the_others(ctx, connected):
    ctx.broker.extra_accounts.append({"id": 1003, "name": "DEMO1003", "active": True})
    await connected.post("/api/connections/A/refresh")

    health = (await connected.post("/api/connections/A/targets/add", json={"account": "DEMO1003"})).json()

    assert [a["name"] for a in health["accounts"] if a["is_target"]] == ["DEMO1001", "DEMO1002", "DEMO1003"]
    assert accounts_by_name(health)["DEMO1003"]["is_new"] is False
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))
    assert sorted(o["accountId"] for o in ctx.broker.order_requests) == [1001, 1002, 1003]


async def test_removing_one_target_keeps_the_others(ctx, connected):
    health = (await connected.post("/api/connections/A/targets/remove", json={"account": "1001"})).json()

    assert [a["name"] for a in health["accounts"] if a["is_target"]] == ["DEMO1002"]


async def test_removing_the_last_target_leaves_no_target(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})

    await connected.post("/api/connections/A/targets/remove", json={"account": "DEMO1001"})

    # not "every account", which is what an empty list means otherwise
    assert ctx.manager.live["A"].target_account_ids() == []


async def test_target_changes_refuse_unknown_accounts(connected):
    add = await connected.post("/api/connections/A/targets/add", json={"account": "NOPE"})
    remove = await connected.post("/api/connections/A/targets/remove", json={"account": "NOPE"})

    assert add.status_code == 400
    assert remove.status_code == 400
    assert (await connected.post("/api/connections/nope/targets/add", json={"account": "DEMO1001"})).status_code == 404


async def test_a_closed_account_fires_an_event(ctx, connected):
    queue = ctx.events.subscribe()
    ctx.broker.closed_accounts.add("DEMO1002")

    await connected.post("/api/connections/A/refresh")

    assert [e["change"] for e in events_of(queue, "accounts")] == ["closed"]


async def test_a_program_can_refresh_and_link_with_its_token(ctx, connected, bot):
    ctx.broker.extra_accounts.append({"id": 1003, "name": "DEMO1003", "active": True})

    assert (await bot.post("/api/connections/A/refresh")).status_code == 200
    assert (await bot.post("/api/connections/A/targets/add", json={"account": "DEMO1003"})).status_code == 200


# ---------- size limits per account ----------

async def test_an_account_with_its_own_limit_refuses_more(ctx, connected):
    response = await connected.put("/api/connections/A/limits", json={"account": "DEMO1001", "max_contracts": {"mnq": 1}})
    assert accounts_by_name(response.json())["DEMO1001"]["max_contracts"] == {"MNQ": 1}

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=2))

    by_name = {r.account_name: r for r in result.results}
    assert by_name["DEMO1001"].success is False
    assert by_name["DEMO1001"].error == "account DEMO1001 is limited to 1 MNQ, this asks for 2. nothing sent"
    assert by_name["DEMO1002"].success is True
    assert [o["accountId"] for o in ctx.broker.order_requests] == [1002]


async def test_an_account_limit_also_counts_brackets(ctx, connected):
    await connected.put("/api/connections/A/limits", json={"account": "DEMO1002", "max_contracts": {"MNQ": 1}})

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=2, stop_loss=10))

    assert [r.success for r in result.results] == [True, False]
    assert [r["accountId"] for r in ctx.broker.strategy_requests] == [1001]


async def test_account_limits_can_be_removed(ctx, connected):
    await connected.put("/api/connections/A/limits", json={"account": "DEMO1001", "max_contracts": {"MNQ": 1}})
    await connected.put("/api/connections/A/limits", json={"account": "DEMO1001", "max_contracts": {}})

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=2))

    assert result.succeeded == 2


async def test_limits_need_a_known_account(connected):
    response = await connected.put("/api/connections/A/limits", json={"account": "NOPE", "max_contracts": {"MNQ": 1}})

    assert response.status_code == 400


# ---------- balances ----------

async def test_balances_rise_after_a_winning_trade(ctx, connected):
    before = {a["name"]: a for a in (await connected.get("/api/accounts")).json()}
    assert before["DEMO1001"]["cash_balance"] == 50000.0
    assert before["DEMO1001"]["realized_pnl_today"] == 0.0

    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "qty": 2})
    ctx.broker.fixed_price = 20010.0
    during = {a["name"]: a for a in (await connected.get("/api/accounts")).json()}
    await connected.post("/api/flatten", json={})
    after = {a["name"]: a for a in (await connected.get("/api/accounts")).json()}

    # 10 points, 2 dollars a point, 2 contracts
    account = after["DEMO1001"]
    assert during["DEMO1001"]["open_pnl"] == 40.0
    assert during["DEMO1001"]["positions"] == [{"contract": "MNQZ6", "net_pos": 2}]
    assert account["realized_pnl_today"] == 40.0
    # the cash balance gets the same 40, less the fees of the 4 contracts that filled
    fees = 4 * (0.25 + 0.37)
    assert round(account["cash_balance"] - 50000.0, 2) == round(40.0 - fees, 2)
    assert account["net_liq"] == account["cash_balance"]
    assert account["positions"] == []
    assert account["working_orders"] == 0
    assert account["trade_date"] is not None
    assert account["risk"]["locked"] is False


async def test_accounts_lists_targets_or_every_account(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})

    targets = (await connected.get("/api/accounts")).json()
    every = (await connected.get("/api/accounts?all=true")).json()

    assert [a["name"] for a in targets] == ["DEMO1001"]
    assert [a["name"] for a in every] == ["DEMO1001", "DEMO1002"]
    assert [a["is_target"] for a in every] == [True, False]


async def test_accounts_never_shows_a_blocked_account(ctx, connected):
    await connected.put("/api/connections/A/blocked", json={"blocked_accounts": ["DEMO1002"]})

    every = (await connected.get("/api/accounts?all=true")).json()

    assert [a["name"] for a in every] == ["DEMO1001"]


async def test_working_orders_are_counted(ctx, connected):
    bracket = {"action": "buy", "symbol": "MNQ", "qty": 1, "take_profits": [{"qty": 1, "points": 10}], "stop_loss": 10}
    await connected.post("/api/signals", json=bracket)

    accounts = (await connected.get("/api/accounts")).json()

    # the take profit and the stop
    assert [a["working_orders"] for a in accounts] == [2, 2]


# ---------- the firm's lock (daily loss limit) ----------

async def test_a_locked_account_refuses_orders_before_sending(ctx, connected):
    queue = ctx.events.subscribe()
    ctx.broker.lock_account(1001)

    accounts = {a["name"]: a for a in (await connected.get("/api/accounts")).json()}
    assert accounts["DEMO1001"]["risk"]["locked"] is True
    assert accounts["DEMO1001"]["risk"]["admin_action"] == "LiquidateOnlyModeByAutoLiq"
    assert accounts["DEMO1002"]["risk"]["locked"] is False

    record = (await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "stop_loss": 10})).json()
    by_name = {r["account_name"]: r for r in record["result"]["results"]}
    assert by_name["DEMO1001"]["error"] == "account DEMO1001 is locked by the firm: LiquidateOnlyModeByAutoLiq. nothing sent"
    assert [r["accountId"] for r in ctx.broker.strategy_requests] == [1002]

    ctx.broker.unlock_account(1001)
    await connected.get("/api/accounts")
    changes = [e for e in events_of(queue, "accounts")]
    assert [(e["name"], e["change"]) for e in changes] == [("DEMO1001", "unlocked")]


async def test_a_locked_account_can_still_go_flat(ctx, connected):
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "position": 1})
    ctx.broker.lock_account(1001)
    await ctx.risk.refresh(ctx.manager.live["A"])

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", position=0))
    flatten = await ctx.dispatcher.run(Signal(action="flatten"))

    assert result.succeeded == 2
    assert flatten.succeeded == 2
    assert open_positions(ctx.broker) == {}


async def test_locking_fires_an_event(ctx, connected):
    conn = ctx.manager.live["A"]
    await ctx.risk.refresh(conn)
    queue = ctx.events.subscribe()

    ctx.broker.lock_account(1002)
    await ctx.risk.refresh(conn)

    assert [(e["name"], e["change"], e["reason"]) for e in events_of(queue, "accounts")] == [
        ("DEMO1002", "locked", "LiquidateOnlyModeByAutoLiq")
    ]

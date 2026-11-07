from app.models import Signal


def positions_of(broker):
    return {key: p["netPos"] for key, p in broker.positions.items() if p["netPos"] != 0}


async def test_buy_goes_to_every_account(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=2))

    assert result.total == 2
    assert result.succeeded == 2
    # MNQ became the front month, not the next contract
    assert result.contract == "MNQZ6"
    for row in result.results:
        assert row.status == "Filled"
        assert row.fill_price == 20000.0
    assert positions_of(ctx.broker) == {(1001, 1): 2, (1002, 1): 2}


async def test_an_exact_contract_is_used_as_it_is(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", contract="MNQH7"))

    assert result.contract == "MNQH7"
    assert ctx.broker.order_requests[0]["symbol"] == "MNQH7"


async def test_limit_uses_price_and_stop_uses_stop_price(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", order_type="limit", price=19900))
    await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", order_type="stop", price=19800))

    limit_order, stop_order = ctx.broker.order_requests[0], ctx.broker.order_requests[2]
    assert limit_order["orderType"] == "Limit"
    assert limit_order["price"] == 19900
    assert "stopPrice" not in limit_order
    assert stop_order["orderType"] == "Stop"
    assert stop_order["stopPrice"] == 19800
    assert "price" not in stop_order


async def test_only_target_accounts_trade(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))

    assert result.total == 1
    assert result.results[0].account_name == "DEMO1001"


async def test_a_refused_order_is_a_failure_with_the_reason(ctx, connected):
    # tradovate answers 200 with a failureReason, that must not count as success
    ctx.broker.refuse_orders = True

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))

    assert result.failed == 2
    assert result.results[0].error == "trading is locked"


async def test_an_order_rejected_after_being_accepted_is_a_failure(ctx, connected):
    ctx.broker.reject_after_accept = True

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))

    assert result.failed == 2
    assert result.results[0].status == "Rejected"


async def test_unknown_connection_gives_a_warning(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", connection="nope"))

    assert result.total == 0
    assert "no connection called 'nope'" in result.warnings[0]


async def test_flatten_closes_everything(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=3))
    await ctx.dispatcher.run(Signal(action="sell", symbol="ES", qty=1))

    result = await ctx.dispatcher.run(Signal(action="flatten"))

    assert result.succeeded == 2
    assert positions_of(ctx.broker) == {}


async def test_flatten_with_a_symbol_only_closes_that_symbol(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=3))
    await ctx.dispatcher.run(Signal(action="sell", symbol="ES", qty=1))

    await ctx.dispatcher.run(Signal(action="flatten", symbol="MNQ"))

    # only the ES short is left (contract 3)
    assert positions_of(ctx.broker) == {(1001, 3): -1, (1002, 3): -1}


async def test_flatten_only_touches_the_target_accounts(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})

    await ctx.dispatcher.run(Signal(action="flatten"))

    # the fake api returns positions of all accounts, we still must not touch the other one
    assert positions_of(ctx.broker) == {(1002, 1): 1}


async def test_flatten_when_flat_is_fine(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="flatten"))

    assert result.succeeded == 2
    assert result.results[0].status == "nothing to close"


async def test_connection_without_accounts_is_skipped_with_a_warning(ctx, connected):
    ctx.manager.live["A"].account_names.clear()

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))

    assert result.total == 0
    assert any("no accounts loaded" in w for w in result.warnings)


# ---------- position mode ----------

async def test_position_from_flat_buys_the_target(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", position=2))

    assert result.succeeded == 2
    assert positions_of(ctx.broker) == {(1001, 1): 2, (1002, 1): 2}


async def test_position_flip_is_one_order(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=2))

    await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", position=-1))

    assert positions_of(ctx.broker) == {(1001, 1): -1, (1002, 1): -1}
    flip = ctx.broker.order_requests[2]
    assert flip["action"] == "Sell"
    assert flip["orderQty"] == 3


async def test_position_catches_up_an_account_that_missed_a_signal(ctx, connected):
    # only one account got the entry
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=1))
    await connected.patch("/api/connections/A", json={"target_accounts": []})

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", position=1))

    assert positions_of(ctx.broker) == {(1001, 1): 1, (1002, 1): 1}
    by_name = {r.account_name: r for r in result.results}
    assert by_name["DEMO1001"].status == "already at 1"
    assert by_name["DEMO1002"].status == "Filled"


async def test_position_zero_closes(ctx, connected):
    await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", qty=2))

    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", position=0))

    assert positions_of(ctx.broker) == {}


async def test_position_only_looks_at_that_contract(ctx, connected):
    await ctx.dispatcher.run(Signal(action="sell", symbol="ES", qty=1))

    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", position=1))

    assert positions_of(ctx.broker) == {(1001, 1): 1, (1002, 1): 1, (1001, 3): -1, (1002, 3): -1}


# ---------- orders are never sent twice ----------

async def test_an_order_without_a_clear_answer_is_not_sent_again(ctx, connected):
    # tradovate placed the order but answered 504
    ctx.broker.order_error_status = 504

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))

    # one request per account, no retries
    assert len(ctx.broker.order_requests) == 2
    assert result.failed == 2
    assert "may or may not have gone through" in result.results[0].error
    assert positions_of(ctx.broker) == {(1001, 1): 1, (1002, 1): 1}

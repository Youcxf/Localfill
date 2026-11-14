import json

import pytest
from pydantic import ValidationError

from app.models import Signal
from tests.conftest import settle

SPLIT = [{"qty": 5, "points": 8}, {"qty": 5, "points": 20}]


def positions_of(broker):
    return {key: p["netPos"] for key, p in broker.positions.items() if p["netPos"] != 0}


# the brackets tradovate got in the first strategy request
def sent_brackets(broker, number=0):
    return json.loads(broker.strategy_requests[number]["params"])


def working(broker, account_id=1001):
    return [o for o in broker.orders.values() if o["accountId"] == account_id and o["ordStatus"] in ("Working", "Suspended")]


# ---------- the alert ----------

def test_brackets_do_not_work_with_position():
    with pytest.raises(ValidationError, match="do not work with position"):
        Signal(action="buy", symbol="MNQ", position=2, stop_loss=10)


def test_stop_loss_and_stop_price_are_not_both_allowed():
    with pytest.raises(ValidationError, match="not both"):
        Signal(action="buy", symbol="MNQ", stop_loss=10, stop_price=19990)


def test_flatten_takes_no_bracket():
    with pytest.raises(ValidationError, match="flatten does not take"):
        Signal(action="flatten", stop_loss=10)


def test_breakeven_needs_a_part_that_goes_further():
    with pytest.raises(ValidationError, match="no part goes further"):
        Signal(action="buy", symbol="MNQ", take_profits=[{"qty": 1, "points": 8}], breakeven_after=1, stop_loss=10)


def test_breakeven_after_a_take_profit_that_does_not_exist():
    with pytest.raises(ValidationError, match="only 2 take profits"):
        Signal(action="buy", symbol="MNQ", take_profits=SPLIT, breakeven_after=3, stop_loss=10)


# ---------- what tradovate gets ----------

async def test_a_buy_with_split_take_profits_and_breakeven(ctx, connected):
    signal = Signal(action="buy", symbol="MNQ", take_profits=SPLIT, stop_loss=12, breakeven_after=1, breakeven_ticks=2)

    result = await ctx.dispatcher.run(signal)

    assert result.succeeded == 2
    assert result.qty == 10
    assert result.bracket == "5 at +8, 5 at +20, stop 12, breakeven after tp 1 +2 ticks"
    request = ctx.broker.strategy_requests[0]
    assert request["action"] == "Buy"
    assert request["orderStrategyTypeId"] == 2
    params = sent_brackets(ctx.broker)
    assert params["entryVersion"] == {"orderQty": 10, "orderType": "Market"}
    # only the part that goes past tp 1 gets breakeven, it ends 2 ticks (0.5) in profit
    assert params["brackets"] == [
        {"qty": 5, "stopLoss": -12, "trailingStop": False, "profitTarget": 8},
        {"qty": 5, "stopLoss": -12, "trailingStop": False, "profitTarget": 20, "breakeven": 8, "breakevenPlus": 0.5},
    ]
    row = result.results[0]
    assert row.status == "Filled"
    assert row.fill_price == 20000.0
    assert row.strategy_id is not None
    assert positions_of(ctx.broker) == {(1001, 1): 10, (1002, 1): 10}


async def test_a_sell_flips_every_sign_but_the_trigger(ctx, connected):
    signal = Signal(action="sell", symbol="MNQ", take_profits=SPLIT, stop_loss=12, breakeven_after=1, breakeven_ticks=2)

    await ctx.dispatcher.run(signal)

    # checked on a real account: a sell wants a negative target and offset, the trigger stays positive
    second = sent_brackets(ctx.broker)["brackets"][1]
    assert second == {"qty": 5, "stopLoss": 12, "trailingStop": False, "profitTarget": -20, "breakeven": 8, "breakevenPlus": -0.5}


async def test_points_are_rounded_to_the_tick(ctx, connected):
    signal = Signal(action="buy", symbol="MNQ", take_profits=[{"qty": 1, "points": 8.1}], stop_loss=11.9)

    await ctx.dispatcher.run(signal)

    bracket = sent_brackets(ctx.broker)["brackets"][0]
    assert bracket["profitTarget"] == 8.0
    assert bracket["stopLoss"] == -12.0


async def test_only_a_stop_uses_the_whole_qty(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=3, stop_loss=10))

    assert result.succeeded == 2
    assert sent_brackets(ctx.broker)["brackets"] == [{"qty": 3, "stopLoss": -10, "trailingStop": False}]


async def test_a_part_without_points_only_has_the_stop(ctx, connected):
    parts = [{"qty": 1, "points": 8}, {"qty": 1}]
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", take_profits=parts, stop_loss=10, breakeven_after=1))

    runner = sent_brackets(ctx.broker)["brackets"][1]
    assert "profitTarget" not in runner
    assert runner["breakeven"] == 8


async def test_a_limit_entry_waits_with_its_brackets(ctx, connected):
    signal = Signal(action="buy", symbol="MNQ", order_type="limit", price=19900, take_profits=SPLIT, stop_loss=10)

    result = await ctx.dispatcher.run(signal)

    assert result.results[0].status == "Working"
    assert sent_brackets(ctx.broker)["entryVersion"] == {"orderQty": 10, "orderType": "Limit", "price": 19900}
    assert {o["ordStatus"] for o in working(ctx.broker) if o["label"] != "256"} == {"Suspended"}
    assert positions_of(ctx.broker) == {}


async def test_flatten_cancels_a_waiting_bracket(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", order_type="limit", price=19900, stop_loss=10))

    await ctx.dispatcher.run(Signal(action="flatten", symbol="MNQ"))

    assert working(ctx.broker) == []


async def test_the_size_limit_counts_every_part(ctx, connected):
    parts = [{"qty": 6, "points": 8}, {"qty": 6, "points": 20}]

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", take_profits=parts, stop_loss=10))

    assert "asks for 12 contracts" in result.warnings[0]
    assert ctx.broker.strategy_requests == []


async def test_a_bracket_needs_a_stop(ctx, connected):
    with pytest.raises(ValueError, match="needs a stop"):
        await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", take_profits=SPLIT))


async def test_a_refused_bracket_is_a_failure_with_the_reason(ctx, connected):
    ctx.broker.refuse_strategies = "Stop and Stop/Limit breakeven supported only"

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", stop_loss=10))

    assert result.failed == 2
    assert result.results[0].error == "Stop and Stop/Limit breakeven supported only"


async def test_missing_take_profits_and_stop_are_reported(ctx, connected):
    ctx.broker.skip_brackets = True

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", take_profits=SPLIT, stop_loss=10))

    assert result.failed == 2
    assert "did not show up" in result.results[0].error


# ---------- the market moving ----------

async def test_tradovate_moves_the_stop_after_the_first_take_profit(ctx, connected):
    signal = Signal(action="buy", symbol="MNQ", take_profits=SPLIT, stop_loss=12, breakeven_after=1, breakeven_ticks=2)
    await ctx.dispatcher.run(signal)

    ctx.broker.move_to(20008.0)

    # 5 left on each account, and their stop sits 2 ticks above the entry
    assert positions_of(ctx.broker) == {(1001, 1): 5, (1002, 1): 5}
    stops = [o for o in working(ctx.broker) if o["orderType"] == "Stop"]
    assert [o["stopPrice"] for o in stops] == [20000.5]


# ---------- exact stop prices ----------

async def test_stop_price_on_a_market_order_is_moved_there_after_the_fill(ctx, connected):
    # the alert's price (its {{close}}) gives the first distance, the fill gives the real one
    signal = Signal(action="buy", symbol="MNQ", price=19995, stop_price=19990, take_profits=SPLIT)

    result = await ctx.dispatcher.run(signal)

    assert sent_brackets(ctx.broker)["brackets"][0]["stopLoss"] == -5
    assert {r["stopPrice"] for r in ctx.broker.modify_requests} == {19990}
    assert len(ctx.broker.modify_requests) == 4
    assert result.results[0].status == "Filled, stop at 19990"


async def test_stop_price_on_a_market_order_without_a_price_needs_a_backup_stop(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", stop_price=19990))

    assert result.failed == 2
    assert "also needs price" in result.results[0].error
    assert ctx.broker.strategy_requests == []


async def test_stop_price_on_a_limit_order_is_measured_from_the_limit(ctx, connected):
    signal = Signal(action="buy", symbol="MNQ", order_type="limit", price=19900, stop_price=19880)

    await ctx.dispatcher.run(signal)

    assert sent_brackets(ctx.broker)["brackets"][0]["stopLoss"] == -20
    assert ctx.broker.modify_requests == []


async def test_stop_price_on_the_wrong_side_sends_nothing(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", price=19995, stop_price=20010))

    assert "wrong side" in result.results[0].error
    assert ctx.broker.strategy_requests == []


# ---------- presets ----------

async def test_presets_can_be_saved_listed_and_deleted(connected):
    preset = {"take_profits": SPLIT, "stop_loss": 15, "breakeven_after": 1, "breakeven_ticks": 2}

    response = await connected.put("/api/presets/nq split", json=preset)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "nq split"

    listed = (await connected.get("/api/presets")).json()
    assert [p["name"] for p in listed] == ["nq split"]
    assert listed[0]["if_open"] == "reverse"

    assert (await connected.delete("/api/presets/nq split")).status_code == 204
    assert (await connected.delete("/api/presets/nq split")).status_code == 404


async def test_a_preset_that_can_not_work_is_refused(connected):
    response = await connected.put("/api/presets/bad", json={"take_profits": SPLIT, "breakeven_after": 2})

    assert response.status_code == 422
    assert "no part goes further" in response.text


async def test_an_alert_uses_the_preset_with_its_own_stop(ctx, connected):
    await connected.put("/api/presets/split", json={"take_profits": SPLIT, "stop_loss": 15, "breakeven_after": 1, "breakeven_ticks": 2})

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=1, preset="split", stop_loss=9))

    # the preset sets the size, the alert's stop wins over the backup one
    assert result.qty == 10
    brackets = sent_brackets(ctx.broker)["brackets"]
    assert [b["stopLoss"] for b in brackets] == [-9, -9]
    assert brackets[1]["breakevenPlus"] == 0.5


async def test_the_backup_stop_is_used_when_the_alert_has_none(ctx, connected):
    await connected.put("/api/presets/split", json={"take_profits": SPLIT, "stop_loss": 15})

    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", preset="split"))

    assert sent_brackets(ctx.broker)["brackets"][0]["stopLoss"] == -15


async def test_the_max_stop_tightens_a_far_stop_from_the_alert(ctx, connected):
    await connected.put("/api/presets/split", json={"take_profits": SPLIT, "max_stop_loss": 20})

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", preset="split", stop_loss=50))

    assert sent_brackets(ctx.broker)["brackets"][0]["stopLoss"] == -20
    assert result.bracket == "5 at +8, 5 at +20, stop 50 (max 20)"


async def test_the_max_stop_also_limits_an_exact_stop_price(ctx, connected):
    await connected.put("/api/presets/split", json={"take_profits": SPLIT, "max_stop_loss": 20})

    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", preset="split", stop_price=19900))

    # no price in the alert, so it starts at the max stop, and moving it would go past the max
    assert sent_brackets(ctx.broker)["brackets"][0]["stopLoss"] == -20
    assert ctx.broker.modify_requests == []


async def test_take_profits_in_the_alert_win_over_the_preset(ctx, connected):
    await connected.put("/api/presets/split", json={"take_profits": SPLIT, "stop_loss": 15})

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", preset="split", take_profits=[{"qty": 2, "points": 4}]))

    assert result.qty == 2
    assert sent_brackets(ctx.broker)["brackets"] == [{"qty": 2, "stopLoss": -15, "trailingStop": False, "profitTarget": 4}]


async def test_an_unknown_preset_is_an_error(ctx, connected, ingress):
    response = await ingress.post(f"/hook/{ctx.webhook_token}", json={"action": "buy", "symbol": "MNQ", "preset": "nope"})
    await settle(ctx)

    record = await ctx.processor.get(response.json()["signal_id"])
    assert record.status == "error"
    assert record.message == "there is no preset called 'nope'"


# ---------- when the account already holds the contract ----------

async def test_an_opposite_bracket_closes_the_position_first(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=2, stop_loss=10))

    result = await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", qty=3, stop_loss=10))

    assert result.results[0].status == "Filled, closed 2 first"
    assert positions_of(ctx.broker) == {(1001, 1): -3, (1002, 1): -3}
    # the long's stop went away with it, only the new short's stop is left
    assert [o["action"] for o in working(ctx.broker)] == ["Buy"]


async def test_a_same_side_bracket_is_added_on_top(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=2, stop_loss=10))

    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=1, stop_loss=10))

    assert positions_of(ctx.broker) == {(1001, 1): 3, (1002, 1): 3}
    assert len(working(ctx.broker)) == 2


async def test_replace_closes_a_same_side_position_too(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=2, stop_loss=10))

    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=1, stop_loss=10, if_open="replace"))

    assert positions_of(ctx.broker) == {(1001, 1): 1, (1002, 1): 1}
    assert len(working(ctx.broker)) == 1


async def test_skip_sends_nothing_to_an_account_in_a_position(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=2, stop_loss=10, accounts=["DEMO1001"]))

    result = await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", qty=1, stop_loss=10, if_open="skip"))

    by_account = {r.account_name: r.status for r in result.results}
    assert by_account["DEMO1001"] == "skipped, it already holds 2"
    assert positions_of(ctx.broker) == {(1001, 1): 2, (1002, 1): -1}


# ---------- plain orders while a bracket is open ----------

async def test_a_plain_order_the_other_way_closes_the_bracket(ctx, connected):
    # tradovate keeps a bracket's orders working after a plain order closes the position,
    # so the strategy's exit closes it with its take profits and stop instead
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", take_profits=SPLIT, stop_loss=10))

    result = await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", qty=10))

    assert result.results[0].status == "closed 10 with its take profits and stop, instead of sell 10"
    assert positions_of(ctx.broker) == {}
    assert working(ctx.broker) == []


async def test_a_plain_order_on_a_flat_account_cancels_leftover_bracket_orders(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", stop_loss=10, accounts=["DEMO1001"]))
    # closed somewhere else (on tradovate's website, say), the stop is left working
    ctx.broker.positions[(1001, 1)]["netPos"] = 0

    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", accounts=["DEMO1001"]))

    assert working(ctx.broker) == []
    assert positions_of(ctx.broker) == {(1001, 1): 1}


async def test_plain_orders_do_not_look_for_brackets_when_there_are_none(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))

    assert ctx.broker.order_requests[0]["orderQty"] == 1
    assert await ctx.brackets.tracked("A", 1001, 1) == []


async def test_a_finished_bracket_is_forgotten(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", stop_loss=10, accounts=["DEMO1001"]))
    ctx.broker.move_to(19990.0)

    await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", accounts=["DEMO1001"]))

    # the stop closed it, so the plain sell is sent as it is
    assert await ctx.brackets.tracked("A", 1001, 1) == []
    assert positions_of(ctx.broker) == {(1001, 1): -1}


async def test_position_zero_closes_the_bracket(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", take_profits=SPLIT, stop_loss=10))

    result = await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", position=0))

    assert result.results[0].status == "closed 10 with its take profits and stop"
    assert working(ctx.broker) == []


async def test_position_on_the_other_side_closes_the_bracket_then_trades(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=2, stop_loss=10))

    result = await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", position=-1))

    assert result.results[0].status == "closed 2 with its take profits and stop, then Filled"
    assert positions_of(ctx.broker) == {(1001, 1): -1, (1002, 1): -1}
    assert working(ctx.broker) == []

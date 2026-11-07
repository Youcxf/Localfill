from datetime import datetime, timedelta, timezone

from app.models import Signal

from .test_dispatcher import positions_of

# ---------- size limit ----------

async def test_an_alert_over_the_size_limit_sends_nothing(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=11))

    assert result.total == 0
    assert "limit is 10" in result.warnings[0]
    assert ctx.broker.order_requests == []


async def test_a_position_over_the_size_limit_sends_nothing(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", position=-11))

    assert result.total == 0
    assert ctx.broker.order_requests == []


async def test_the_size_limit_itself_is_allowed(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", qty=10))

    assert result.succeeded == 2


# ---------- slow position updates ----------

async def test_a_slow_position_list_does_not_cause_a_second_order(ctx, connected):
    # tradovate shows the old position for a few reads after each fill
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})
    ctx.broker.stale_reads_after_fill = 3

    first = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", position=1))
    second = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", position=1))

    assert all("not confirmed" not in (r.status or "") for r in first.results)
    assert all(r.status == "already at 1" for r in second.results)
    assert len(ctx.broker.order_requests) == 1
    assert positions_of(ctx.broker) == {(1001, 1): 1}


# ---------- late alerts ----------

async def test_an_alert_that_arrives_late_is_ignored(ctx):
    now = datetime.now(timezone.utc)
    newer = Signal(action="buy", symbol="MNQ", position=1, sent_at=now)
    older = Signal(action="sell", symbol="MNQ", position=0, sent_at=now - timedelta(seconds=5))

    _, run_newer = await ctx.processor.receive(newer, "webhook")
    record, run_older = await ctx.processor.receive(older, "webhook")

    assert run_newer
    assert not run_older
    assert "newer alert" in record.message


async def test_late_alerts_only_compare_the_same_symbol(ctx):
    now = datetime.now(timezone.utc)
    await ctx.processor.receive(Signal(action="buy", symbol="MNQ", sent_at=now), "webhook")

    _, should_run = await ctx.processor.receive(Signal(action="buy", symbol="ES", sent_at=now - timedelta(seconds=5)), "webhook")

    assert should_run


# ---------- choosing accounts per alert ----------

async def test_an_alert_can_name_its_accounts(ctx, connected):
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", accounts=["DEMO1002"]))

    assert [r.account_name for r in result.results] == ["DEMO1002"]


async def test_an_alert_can_not_reach_an_account_that_is_not_a_target(ctx, connected):
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1001"]})

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", accounts=["DEMO1002"]))

    assert result.total == 0


# ---------- duplicates ----------

async def test_identical_position_alerts_are_never_dropped(ctx):
    ctx.settings.dedupe_seconds = 2
    signal = Signal(action="buy", symbol="MNQ", position=1)

    _, first = await ctx.processor.receive(signal, "webhook")
    _, second = await ctx.processor.receive(signal, "webhook")

    assert first and second


async def test_identical_qty_alerts_are_still_dropped(ctx):
    ctx.settings.dedupe_seconds = 2
    signal = Signal(action="buy", symbol="MNQ", qty=1)

    await ctx.processor.receive(signal, "webhook")
    record, second = await ctx.processor.receive(signal, "webhook")

    assert not second
    assert record.status == "duplicate"


# ---------- signals left pending by a crash ----------

async def test_signals_left_pending_become_errors(ctx):
    record = await ctx.processor.save(Signal(action="buy", symbol="MNQ"), "webhook", "pending")

    await ctx.processor.close_unfinished()

    after = await ctx.processor.get(record.id)
    assert after.status == "error"
    assert "check the accounts" in after.message


# ---------- new and closed accounts ----------

async def test_a_new_account_gets_no_orders_until_selected(ctx, connected):
    conn = ctx.manager.live["A"]
    ctx.broker.extra_accounts.append({"id": 1003, "name": "DEMO1003", "active": True})
    await conn.refresh_accounts()

    assert sorted(conn.target_account_ids()) == [1001, 1002]

    # choosing "every account" again accepts it
    await connected.patch("/api/connections/A", json={"target_accounts": []})
    assert sorted(conn.target_account_ids()) == [1001, 1002, 1003]


async def test_a_closed_account_gets_no_orders(ctx, connected):
    conn = ctx.manager.live["A"]
    ctx.broker.closed_accounts.add("DEMO1002")
    await conn.refresh_accounts()

    assert conn.target_account_ids() == [1001]

    # not even when it is picked by name
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1002"]})
    assert conn.target_account_ids() == []


async def test_known_accounts_survive_a_restart(ctx, connected):
    row = await ctx.db.fetch_one("select known_accounts from connections where label = 'A'")
    assert row["known_accounts"] == '["DEMO1001", "DEMO1002"]'


# ---------- rollover ----------

async def test_position_mode_stops_when_last_month_is_still_open(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", contract="MNQH7"))
    orders_before = len(ctx.broker.order_requests)

    # MNQ means MNQZ6 here, the account still holds MNQH7
    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", position=1))

    assert result.failed == 2
    assert "still holds MNQH7" in result.results[0].error
    assert len(ctx.broker.order_requests) == orders_before


# ---------- flatten and working orders ----------

async def test_flatten_cancels_working_orders(ctx, connected):
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ", order_type="limit", price=19000))
    await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))

    result = await ctx.dispatcher.run(Signal(action="flatten"))

    assert len(ctx.broker.cancelled_orders) == 2
    assert positions_of(ctx.broker) == {}
    assert "cancelled 1 order(s)" in result.results[0].status


async def test_flatten_with_only_working_orders(ctx, connected):
    await ctx.dispatcher.run(Signal(action="sell", symbol="MNQ", order_type="limit", price=21000))

    result = await ctx.dispatcher.run(Signal(action="flatten"))

    assert result.succeeded == 2
    assert result.results[0].status == "cancelled 1 order(s), nothing to close"


# ---------- tradovate throttling ----------

async def test_a_throttled_order_is_sent_again_with_the_ticket(ctx, connected):
    ctx.broker.penalize_next_order = True

    result = await ctx.dispatcher.run(Signal(action="buy", symbol="MNQ"))

    assert result.succeeded == 2
    assert any(o.get("p-ticket") == "penalty-ticket" for o in ctx.broker.order_requests)
    assert positions_of(ctx.broker) == {(1001, 1): 1, (1002, 1): 1}


# ---------- login loops ----------

async def test_logging_in_again_is_capped(ctx, connected):
    conn = ctx.manager.live["A"]
    assert [conn.too_many_relogins() for _ in range(4)] == [False, False, False, True]

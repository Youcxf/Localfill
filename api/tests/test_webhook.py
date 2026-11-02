from .conftest import settle


def hook_url(ctx):
    return f"/hook/{ctx.webhook_token}"


async def test_health_is_open(ingress):
    response = await ingress.get("/health")
    assert response.status_code == 200


async def test_wrong_token_looks_like_a_missing_page(ingress):
    response = await ingress.post("/hook/not-the-token", json={"action": "buy", "symbol": "MNQ"})
    assert response.status_code == 404


async def test_ingress_has_no_dashboard_routes(ctx, ingress):
    # the public server must not expose any admin route
    for path in ("/api/connections", "/api/positions", "/docs", "/openapi.json"):
        assert (await ingress.get(path)).status_code == 404


async def test_body_must_be_json(ctx, ingress):
    response = await ingress.post(hook_url(ctx), content="buy mnq please")
    assert response.status_code == 400


async def test_a_huge_body_is_refused(ctx, ingress):
    response = await ingress.post(hook_url(ctx), content="x" * 10_001)
    assert response.status_code == 413


async def test_invalid_signal_says_what_is_wrong(ctx, ingress):
    response = await ingress.post(hook_url(ctx), json={"action": "buy"})
    assert response.status_code == 422
    assert response.json()["detail"] == "symbol is required for buy and sell"

    # several problems come back in one line
    response = await ingress.post(hook_url(ctx), json={"action": "hold", "symbol": "MNQ", "qty": 0})
    detail = response.json()["detail"]
    assert "action: Input should be 'buy', 'sell' or 'flatten'" in detail
    assert "qty: Input should be greater than or equal to 1" in detail


async def test_a_valid_signal_is_answered_fast_and_run_in_the_background(ctx, connected, ingress, admin):
    response = await ingress.post(hook_url(ctx), json={"action": "Buy", "symbol": "MNQ1!", "qty": 2})
    assert response.status_code == 202
    signal_id = response.json()["signal_id"]

    await settle(ctx)

    record = (await admin.get(f"/api/signals/{signal_id}")).json()
    assert record["status"] == "done"
    assert record["source"] == "webhook"
    assert record["result"]["succeeded"] == 2
    assert record["payload"]["symbol"] == "MNQ"


async def test_tradingview_sends_json_as_plain_text(ctx, connected, ingress):
    response = await ingress.post(
        hook_url(ctx),
        content='{"action": "buy", "symbol": "MNQ"}',
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    assert response.status_code == 202
    await settle(ctx)


async def test_a_signal_can_fail_and_the_history_says_why(ctx, connected, ingress, admin):
    ctx.broker.refuse_orders = True

    response = await ingress.post(hook_url(ctx), json={"action": "buy", "symbol": "MNQ"})
    await settle(ctx)

    record = (await admin.get(f"/api/signals/{response.json()['signal_id']}")).json()
    assert record["status"] == "failed"
    assert record["message"] == "trading is locked"


async def test_duplicate_signals_are_ignored(ctx, connected, ingress, admin):
    ctx.settings.dedupe_seconds = 5
    body = {"action": "buy", "symbol": "MNQ"}

    first = await ingress.post(hook_url(ctx), json=body)
    second = await ingress.post(hook_url(ctx), json=body)
    await settle(ctx)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    # only one order per account reached tradovate
    assert len(ctx.broker.order_requests) == 2
    # but both are in the history
    assert len((await admin.get("/api/signals")).json()) == 2


async def test_dry_run_sends_nothing(ctx, connected, ingress, admin):
    await admin.patch("/api/settings", json={"dry_run": True})

    response = await ingress.post(hook_url(ctx), json={"action": "buy", "symbol": "MNQ"})
    await settle(ctx)

    record = (await admin.get(f"/api/signals/{response.json()['signal_id']}")).json()
    assert record["status"] == "dry_run"
    assert ctx.broker.order_requests == []


async def test_signals_run_in_the_order_they_arrived(ctx, connected, ingress):
    # if these two ran at the same time the flatten would look for positions
    # while the buy is still being placed, find none, and leave the position open
    ctx.broker.order_delay = 0.3
    await ingress.post(hook_url(ctx), json={"action": "buy", "symbol": "MNQ", "qty": 2})
    await ingress.post(hook_url(ctx), json={"action": "flatten"})
    await settle(ctx)

    assert all(p["netPos"] == 0 for p in ctx.broker.positions.values())
    assert len(ctx.broker.positions) == 2

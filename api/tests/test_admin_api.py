import httpx

from app.context import AppContext
from app.webapp import create_admin_app

from .conftest import PASSWORD, make_settings

# ---------- login ----------

async def test_first_run_asks_for_a_password(ctx):
    transport = httpx.ASGITransport(app=create_admin_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        status = (await client.get("/api/auth/status")).json()
        assert status == {"setup_required": True, "logged_in": False}


async def test_protected_routes_need_a_login(ctx):
    transport = httpx.ASGITransport(app=create_admin_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        paths = [
            "/api/connections", "/api/positions", "/api/pnl", "/api/signals", "/api/signals/counts",
            "/api/settings", "/api/status", "/api/events",
        ]
        for path in paths:
            assert (await client.get(path)).status_code == 401, path
        # the healthcheck stays open
        assert (await client.get("/api/health")).status_code == 200


async def test_docs_page_uses_the_localfill_icon(ctx):
    transport = httpx.ASGITransport(app=create_admin_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/docs")
        assert response.status_code == 200
        assert 'href="/icon.png"' in response.text
        assert "fastapi.tiangolo.com" not in response.text


async def test_setup_then_logout_then_login(ctx):
    transport = httpx.ASGITransport(app=create_admin_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/api/auth/setup", json={"password": "short"})).status_code == 422

        assert (await client.post("/api/auth/setup", json={"password": PASSWORD})).status_code == 200
        assert (await client.get("/api/status")).status_code == 200

        # the password can only be chosen once
        assert (await client.post("/api/auth/setup", json={"password": "another one"})).status_code == 409

        await client.post("/api/auth/logout")
        assert (await client.get("/api/status")).status_code == 401

        assert (await client.post("/api/auth/login", json={"password": "wrong password"})).status_code == 401
        assert (await client.post("/api/auth/login", json={"password": PASSWORD})).status_code == 200
        assert (await client.get("/api/status")).status_code == 200


async def test_the_password_is_not_stored_as_text(ctx, admin):
    stored = await ctx.db.get_setting("admin_password")
    assert PASSWORD not in stored


# ---------- connections ----------

async def test_add_connection(connected):
    connections = (await connected.get("/api/connections")).json()

    assert len(connections) == 1
    assert connections[0]["label"] == "A"
    assert connections[0]["authenticated"] is True
    assert [a["name"] for a in connections[0]["accounts"]] == ["DEMO1001", "DEMO1002"]
    assert all(a["is_target"] for a in connections[0]["accounts"])
    # never send the password back
    assert "password" not in connections[0]


async def test_a_failed_login_is_not_saved(ctx, admin):
    response = await admin.post("/api/connections", json={"label": "B", "username": "baduser", "password": "secret123"})

    assert response.status_code == 400
    assert "Incorrect username or password" in response.json()["detail"]
    assert (await admin.get("/api/connections")).json() == []
    assert await ctx.db.fetch_all("select * from connections") == []


async def test_duplicate_label_is_refused(connected):
    response = await connected.post("/api/connections", json={"label": "A", "username": "x", "password": "secret123"})
    assert response.status_code == 409


async def test_passwords_are_encrypted_in_the_database(ctx, connected):
    row = (await ctx.db.fetch_all("select * from connections"))[0]

    assert "secret123" not in row["password_enc"]
    assert ctx.crypto.decrypt(row["password_enc"]) == "secret123"


async def test_connections_come_back_after_a_restart(ctx, connected, tmp_path, broker_server):
    _, server = broker_server
    await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1002"]})

    # a second app on the same data folder, like restarting the container.
    # the first one lets go of the data folder lock, as if it had stopped
    ctx.lock.release()
    second = AppContext(make_settings(tmp_path, server))
    await second.start()
    try:
        conn = second.manager.live["A"]
        assert conn.authenticated
        assert conn.target_account_ids() == [1002]
    finally:
        await second.stop()


async def test_change_targets(connected):
    response = await connected.patch("/api/connections/A", json={"target_accounts": ["DEMO1002"]})

    accounts = {a["name"]: a["is_target"] for a in response.json()["accounts"]}
    assert accounts == {"DEMO1001": False, "DEMO1002": True}


async def test_delete_connection(ctx, connected):
    assert (await connected.delete("/api/connections/A")).status_code == 204
    assert (await connected.get("/api/connections")).json() == []
    assert await ctx.db.fetch_all("select * from connections") == []
    assert (await connected.delete("/api/connections/A")).status_code == 404


async def test_reconnect(connected):
    response = await connected.post("/api/connections/A/reconnect")
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert (await connected.post("/api/connections/nope/reconnect")).status_code == 404


# ---------- trading ----------

async def test_send_a_signal_from_the_dashboard(connected):
    response = await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "qty": 1})

    record = response.json()
    assert record["status"] == "done"
    assert record["source"] == "dashboard"
    assert record["result"]["total"] == 2


async def test_positions_and_pnl(ctx, connected):
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ", "qty": 2})

    positions = (await connected.get("/api/positions")).json()
    assert len(positions["positions"]) == 2
    assert positions["positions"][0]["net_pos"] == 2
    assert positions["distinct_contracts"] == ["MNQZ6"]

    # price moves 10 points, MNQ is 2 dollars a point, 2 contracts: 40 dollars per account
    ctx.broker.fixed_price = 20010.0
    await connected.post("/api/flatten", json={})

    pnl = (await connected.get("/api/pnl")).json()
    assert [a["realized_pnl"] for a in pnl["accounts"]] == [40.0, 40.0]
    assert pnl["accounts"][0]["fill_pairs"][0]["sell_price"] == 20010.0
    assert (await connected.get("/api/positions")).json()["positions"] == []


async def test_signal_history_is_newest_first(connected):
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})
    await connected.post("/api/flatten", json={})

    signals = (await connected.get("/api/signals")).json()

    assert [s["payload"]["action"] for s in signals] == ["flatten", "buy"]
    assert (await connected.get("/api/signals?limit=1")).json()[0]["id"] == signals[0]["id"]
    assert (await connected.get("/api/signals/9999")).status_code == 404


async def test_signals_can_be_filtered_by_status(ctx, connected):
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})
    ctx.broker.refuse_orders = True
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})

    done = (await connected.get("/api/signals?status=done")).json()
    failed = (await connected.get("/api/signals?status=failed")).json()

    assert [s["status"] for s in done] == ["done"]
    assert [s["status"] for s in failed] == ["failed"]
    assert len((await connected.get("/api/signals")).json()) == 2
    # a status that does not exist is refused
    assert (await connected.get("/api/signals?status=nope")).status_code == 422


async def test_signal_counts_per_status(ctx, connected):
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})
    ctx.broker.refuse_orders = True
    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})

    counts = (await connected.get("/api/signals/counts")).json()

    assert counts["total"] == 3
    assert counts["done"] == 2
    assert counts["failed"] == 1
    # states nobody used are still there, as zero
    assert counts["dry_run"] == 0
    assert counts["pending"] == 0


# ---------- settings ----------

async def test_settings_show_the_webhook_url(ctx, admin):
    ctx.settings.public_url = "https://example.com/"

    settings = (await admin.get("/api/settings")).json()

    assert settings["webhook_path"] == f"/hook/{ctx.webhook_token}"
    assert settings["webhook_url"] == f"https://example.com/hook/{ctx.webhook_token}"
    assert settings["dry_run"] is False


async def test_a_new_webhook_token_replaces_the_old_one(ctx, admin, ingress):
    old_token = ctx.webhook_token

    response = await admin.post("/api/settings/webhook-token/regenerate")

    assert response.json()["webhook_token"] != old_token
    assert (await ingress.post(f"/hook/{old_token}", json={"action": "flatten"})).status_code == 404
    assert (await ingress.post(f"/hook/{ctx.webhook_token}", json={"action": "flatten"})).status_code == 202


async def test_the_status_summary(ctx, connected):
    status = (await connected.get("/api/status")).json()

    assert status["connections_total"] == 1
    assert status["connections_online"] == 1
    assert status["dry_run"] is False


# ---------- events ----------

async def test_events_reach_subscribers(ctx):
    queue = ctx.events.subscribe()
    ctx.events.publish("connections")

    assert queue.get_nowait() == {"type": "connections", "data": None}

    ctx.events.unsubscribe(queue)
    ctx.events.publish("connections")
    assert queue.empty()


async def test_signals_are_published_as_events(ctx, connected):
    queue = ctx.events.subscribe()

    await connected.post("/api/signals", json={"action": "buy", "symbol": "MNQ"})

    statuses = []
    while not queue.empty():
        event = queue.get_nowait()
        assert event["type"] == "signal"
        statuses.append(event["data"]["status"])
    # first it is received, then it is done
    assert statuses == ["pending", "done"]


# ---------- frontend files ----------

async def test_angular_files_are_served_with_a_fallback(ctx, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>angular</html>")
    (web / "main.js").write_text("console.log(1)")
    ctx.settings.static_dir = str(web)

    transport = httpx.ASGITransport(app=create_admin_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert "angular" in (await client.get("/")).text
        assert (await client.get("/main.js")).text == "console.log(1)"
        # a route that only exists inside angular
        assert "angular" in (await client.get("/connections")).text
        # but an api typo is a real 404, not the html page
        assert (await client.get("/api/nope")).status_code == 404

import asyncio
from types import SimpleNamespace

import pytest

from app.models import ConnectionConfig
from app.tradovate.auth import build_login_payload, encode_password, make_sec
from app.tradovate.client import TradovateAuthError, TradovateConnection

from .conftest import make_settings


def make_settings_for_urls():
    return SimpleNamespace(tradovate_demo_url="https://demo.example.com/v1", tradovate_live_url="https://live.example.com/v1")


def test_encode_password():
    # "abcd" rotated left by len("ab") % 4 = 2 gives "cdab", reversed "badc", base64 of that
    assert encode_password("ab", "abcd") == "YmFkYw=="


def test_sec_is_a_sha256_hex_and_does_not_change():
    first = make_sec("123", "device", "user", "pass", "app")
    assert first == make_sec("123", "device", "user", "pass", "app")
    assert len(first) == 64
    assert first != make_sec("124", "device", "user", "pass", "app")


def test_web_login_payload_hides_the_plain_password():
    config = ConnectionConfig(label="A", username="user", password="secret123")
    payload = build_login_payload(config, "1.0")
    assert payload["password"] != "secret123"
    assert payload["enc"] is True
    assert payload["environment"] == "demo"


def test_api_key_login_payload():
    config = ConnectionConfig(label="A", username="user", password="secret123", auth_mode="api_key", cid="8", api_secret="abc")
    payload = build_login_payload(config, "1.0")
    assert payload["password"] == "secret123"
    assert payload["cid"] == 8
    assert payload["sec"] == "abc"


async def make_connection(tmp_path, server, username="someone"):
    config = ConnectionConfig(label="A", username=username, password="secret123")
    return TradovateConnection(config, make_settings(tmp_path, server))


def test_api_hosts_from_the_login_answer_are_used():
    hosts = {"demo": "demo-api.lucid.ninjatrader.com", "live": "live.tradovateapi.com"}
    settings = make_settings_for_urls()

    demo = TradovateConnection(ConnectionConfig(label="A", username="u", password="p"), settings)
    demo.use_api_hosts(hosts)
    assert demo.base_url == "https://demo-api.lucid.ninjatrader.com/v1/"
    # logging in again must still start from the normal host
    assert demo.login_url == "https://demo.example.com/v1/"

    live = TradovateConnection(ConnectionConfig(label="B", username="u", password="p", demo=False), settings)
    live.use_api_hosts(hosts)
    assert live.base_url == "https://live.tradovateapi.com/v1/"


def test_no_api_hosts_keeps_the_normal_host():
    conn = TradovateConnection(ConnectionConfig(label="A", username="u", password="p"), make_settings_for_urls())
    conn.use_api_hosts(None)
    assert conn.base_url == "https://demo.example.com/v1/"


async def test_login_loads_the_accounts(tmp_path, broker_server):
    _, server = broker_server
    conn = await make_connection(tmp_path, server)
    await conn.start()
    try:
        assert conn.authenticated
        assert sorted(conn.account_names.values()) == ["DEMO1001", "DEMO1002"]
    finally:
        await conn.stop()


async def test_wrong_login_raises_and_keeps_the_error(tmp_path, broker_server):
    _, server = broker_server
    conn = await make_connection(tmp_path, server, username="baduser")
    with pytest.raises(TradovateAuthError):
        await conn.start()
    assert "Incorrect username or password" in conn.last_error
    await conn.stop()


async def test_login_waits_when_tradovate_throttles(tmp_path, broker_server):
    broker, server = broker_server
    broker.throttle_next_login = True
    conn = await make_connection(tmp_path, server)
    await conn.start()
    try:
        assert conn.authenticated
        # first try got the ticket, second try sent it back
        assert len(broker.logins) == 2
        assert broker.logins[1]["p-ticket"] == "ticket-abc"
    finally:
        await conn.stop()


async def test_logs_in_again_after_a_401(tmp_path, broker_server):
    broker, server = broker_server
    conn = await make_connection(tmp_path, server)
    await conn.start()
    try:
        # pretend tradovate forgot our token
        broker.tokens.clear()
        accounts = await conn.request("GET", "/account/list")
        assert len(accounts) == 2
        assert len(broker.logins) == 2
    finally:
        await conn.stop()


async def test_many_requests_with_a_dead_token_log_in_only_once(tmp_path, broker_server):
    broker, server = broker_server
    conn = await make_connection(tmp_path, server)
    await conn.start()
    try:
        broker.tokens.clear()
        await asyncio.gather(*(conn.request("GET", "/account/list") for _ in range(5)))
        # one login at the start plus one to replace the dead token
        assert len(broker.logins) == 2
    finally:
        await conn.stop()

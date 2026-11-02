import pytest
from pydantic import ValidationError

from app.models import ConnectionConfig, Signal


def test_action_and_type_are_lowercased():
    signal = Signal(action="Buy", symbol="MNQ", order_type="Market")
    assert signal.action == "buy"
    assert signal.order_type == "market"


def test_tradingview_symbols_are_cleaned():
    assert Signal(action="buy", symbol="MNQ1!").symbol == "MNQ"
    assert Signal(action="buy", symbol="CME_MINI:MNQ1!").symbol == "MNQ"
    assert Signal(action="buy", symbol=" mnq ").symbol == "MNQ"
    # a full ticker stays as it is
    assert Signal(action="buy", symbol="MNQZ6").symbol == "MNQZ6"


def test_buy_needs_a_symbol():
    with pytest.raises(ValidationError):
        Signal(action="buy")


def test_limit_and_stop_need_a_price():
    with pytest.raises(ValidationError):
        Signal(action="buy", symbol="MNQ", order_type="limit")
    with pytest.raises(ValidationError):
        Signal(action="sell", symbol="MNQ", order_type="stop")
    assert Signal(action="buy", symbol="MNQ", order_type="limit", price=100).price == 100


def test_flatten_needs_nothing():
    signal = Signal(action="flatten")
    assert signal.symbol is None


def test_qty_must_be_positive():
    with pytest.raises(ValidationError):
        Signal(action="buy", symbol="MNQ", qty=0)


def test_extra_fields_from_tradingview_are_ignored():
    signal = Signal(action="buy", symbol="MNQ", time="2026-01-01T00:00:00Z")
    assert signal.symbol == "MNQ"


def test_api_key_mode_needs_cid_and_secret():
    with pytest.raises(ValidationError):
        ConnectionConfig(label="A", username="u", password="p", auth_mode="api_key")
    ConnectionConfig(label="A", username="u", password="p", auth_mode="api_key", cid="8", api_secret="x")


def test_label_cannot_hold_a_slash():
    with pytest.raises(ValidationError):
        ConnectionConfig(label="a/b", username="u", password="p")

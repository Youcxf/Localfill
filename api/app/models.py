import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# base for everything we send back. it makes the api docs say that fields with a
# default (like an empty list) are always present in the answer, so the generated
# angular types do not mark them as optional
class Output(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


# the states a signal can be in (see SignalRecord)
SignalStatus = Literal["pending", "done", "partial", "failed", "dry_run", "duplicate", "error"]
SIGNAL_STATUSES = ["pending", "done", "partial", "failed", "dry_run", "duplicate", "error"]


# ---------- signals: what tradingview sends us ----------

class Signal(BaseModel):
    action: Literal["buy", "sell", "flatten"]
    symbol: str | None = None
    qty: int = Field(default=1, ge=1)
    order_type: Literal["market", "limit", "stop"] = "market"
    price: float | None = None
    # exact ticker like MNQZ6, skips the front month lookup
    contract: str | None = None
    # only trade on this connection, empty means all of them
    connection: str | None = None
    is_automated: bool = True
    # the position every account should end up with (positive long, negative short, 0 flat).
    # when set, qty is ignored: each account sends whatever gets it there, so accounts that
    # missed a signal or only partly filled catch up on the next one
    position: int | None = None
    # when tradingview sent the alert ({{timenow}}). an alert older than the last one we ran
    # for the same symbol arrived late and is skipped, so a late "flat" can not undo a "long"
    sent_at: datetime | None = None
    # only these account names (or ids), instead of every target account of the connection.
    # lets two strategies trade the same symbol on different accounts
    accounts: list[str] | None = None

    # tradingview sends "buy" but some people write "Buy", so lowercase it
    @field_validator("action", "order_type", mode="before")
    @classmethod
    def lowercase(cls, value):
        if isinstance(value, str):
            return value.strip().lower()
        return value

    # CME_MINI:MNQ1! and mnq both become MNQ
    @field_validator("symbol", "contract", mode="before")
    @classmethod
    def clean_symbol(cls, value):
        if not isinstance(value, str):
            return value
        value = value.split(":")[-1].strip().upper()
        if value.endswith(("1!", "2!")):
            value = value[:-2]
        return value or None

    @model_validator(mode="after")
    def check_fields(self):
        if self.action == "flatten":
            if self.position is not None:
                raise ValueError("flatten does not take a position, use position 0 instead")
            return self
        if not self.symbol:
            raise ValueError("symbol is required for buy and sell")
        if self.order_type in ("limit", "stop") and self.price is None:
            raise ValueError("price is required for limit and stop orders")
        if self.position is not None and self.order_type != "market":
            raise ValueError("position only works with market orders")
        return self


# ---------- results ----------

# what happened on one tradovate account
class AccountResult(Output):
    connection: str
    account_id: int
    account_name: str
    success: bool
    latency_ms: float
    order_id: int | None = None
    # tradovate's real order status: Working, Filled, Rejected...
    status: str | None = None
    fill_price: float | None = None
    error: str | None = None
    # the raw tradovate answer, handy when something gets rejected
    response: dict | None = None


# what happened for a whole signal (all accounts together)
class SignalResult(Output):
    action: str
    symbol: str | None = None
    contract: str | None = None
    total: int
    succeeded: int
    failed: int
    results: list[AccountResult] = Field(default_factory=list)
    # things that went wrong before any order was sent, like a connection being offline
    warnings: list[str] = Field(default_factory=list)


# one row of the signal history
class SignalRecord(Output):
    id: int
    received_at: str
    source: Literal["webhook", "dashboard"]
    payload: dict
    # pending = running, done = all accounts worked, partial = some failed, failed = none worked,
    # dry_run = dry run was on, duplicate = ignored, error = broke before any order was sent
    status: SignalStatus
    message: str | None = None
    result: SignalResult | None = None


# how many signals are in each state, for the tabs above the history table
class SignalCounts(Output):
    total: int
    pending: int
    done: int
    partial: int
    failed: int
    dry_run: int
    duplicate: int
    error: int


# ---------- connections ----------

class ConnectionConfig(BaseModel):
    label: str = Field(min_length=1, max_length=50, pattern=r"^[\w .-]+$")
    username: str
    password: str
    demo: bool = True
    # web = log in like tradovate's website does (works without an api subscription)
    # api_key = the official way, needs cid and api_secret from your tradovate api key
    auth_mode: Literal["web", "api_key"] = "web"
    cid: str | None = None
    api_secret: str | None = None
    device_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # account names or ids to trade on, empty means every active account
    target_accounts: list[str] = Field(default_factory=list)
    # filled in by localfill: the account names that existed when the targets were chosen.
    # with empty target_accounts only these are traded, so a new account is never traded by surprise
    known_accounts: list[str] | None = None

    @model_validator(mode="after")
    def check_api_key(self):
        if self.auth_mode == "api_key" and not (self.cid and self.api_secret):
            raise ValueError("api_key mode needs cid and api_secret")
        return self


class TargetsUpdate(BaseModel):
    target_accounts: list[str]


class AccountInfo(Output):
    id: int
    name: str
    active: bool
    # true if orders are sent to this account (see target_accounts)
    is_target: bool


class ConnectionHealth(Output):
    label: str
    demo: bool
    auth_mode: str
    authenticated: bool
    user_id: int | None = None
    token_expires_at: str | None = None
    seconds_to_expiry: float | None = None
    accounts: list[AccountInfo] = Field(default_factory=list)
    last_error: str | None = None


# ---------- positions and pnl ----------

class OpenPosition(Output):
    connection: str
    account_id: int
    account_name: str
    contract_id: int
    contract_name: str
    net_pos: float


class PositionsResponse(Output):
    positions: list[OpenPosition] = Field(default_factory=list)
    # more than one name here means a rollover left you in two contract months
    distinct_contracts: list[str] = Field(default_factory=list)


class FillPair(Output):
    buy_price: float
    sell_price: float
    qty: float
    ts: str | None = None


class AccountPnl(Output):
    connection: str
    account_id: int
    account_name: str
    # realized pnl of the current trade date, straight from tradovate
    realized_pnl: float | None = None
    trade_date: str | None = None
    fill_pairs: list[FillPair] = Field(default_factory=list)


class PnlResponse(Output):
    accounts: list[AccountPnl] = Field(default_factory=list)


# ---------- auth, settings, status ----------

class SetupBody(BaseModel):
    password: str = Field(min_length=8)


class LoginBody(BaseModel):
    password: str


class AuthStatus(Output):
    # true until the admin password has been chosen
    setup_required: bool
    logged_in: bool


class SettingsResponse(Output):
    dry_run: bool
    webhook_token: str
    webhook_path: str
    # only filled in if LOCALFILL_PUBLIC_URL is set
    webhook_url: str | None = None


class SettingsUpdate(BaseModel):
    dry_run: bool | None = None


class StatusResponse(Output):
    version: str
    dry_run: bool
    connections_total: int
    connections_online: int


class FlattenBody(BaseModel):
    symbol: str | None = None
    connection: str | None = None

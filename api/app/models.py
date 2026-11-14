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


# ---------- brackets: take profits and a stop that tradovate places with the entry ----------

# what to do when an account already holds the contract a bracket signal trades.
# reverse = close an opposite position first, add to a same side one. replace = close any
# position first. skip = send nothing to that account
IfOpen = Literal["reverse", "replace", "skip"]


# one part of the entry: qty contracts that leave at points from the fill.
# without points the part only has the stop (a runner)
class TakeProfit(BaseModel):
    qty: int = Field(ge=1)
    points: float | None = Field(default=None, gt=0)


# a bracket saved in the dashboard, alerts use it with "preset": "<name>"
class PresetConfig(BaseModel):
    take_profits: list[TakeProfit] = Field(default_factory=list, max_length=10)
    # backup stop in points, used when the alert sends no stop
    stop_loss: float | None = Field(default=None, gt=0)
    # the farthest stop allowed in points, a farther stop from an alert is moved in to it
    max_stop_loss: float | None = Field(default=None, gt=0)
    # once take profit number n is reached, the stops of the parts that go further move to the entry
    breakeven_after: int | None = Field(default=None, ge=1)
    # how many ticks past the entry that moved stop goes (2 = locks in 2 ticks)
    breakeven_ticks: int = 0
    if_open: IfOpen = "reverse"

    @model_validator(mode="after")
    def check_preset(self):
        check_breakeven(self.take_profits, self.breakeven_after)
        if self.stop_loss and self.max_stop_loss and self.stop_loss > self.max_stop_loss:
            raise ValueError("the backup stop is farther than the max stop")
        return self


class Preset(PresetConfig):
    name: str = Field(min_length=1, max_length=50, pattern=r"^[\w .-]+$")


# breakeven needs a take profit to wait for and a part that goes further than it
def check_breakeven(take_profits, breakeven_after):
    if breakeven_after is None:
        return
    if breakeven_after > len(take_profits):
        raise ValueError(f"breakeven_after is {breakeven_after} but there are only {len(take_profits)} take profits")
    trigger = take_profits[breakeven_after - 1].points
    if trigger is None:
        raise ValueError(f"take profit {breakeven_after} has no points, so breakeven can not wait for it")
    if not any(part.points is None or part.points > trigger for part in take_profits):
        raise ValueError(f"no part goes further than take profit {breakeven_after}, so there is no stop to move")


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

    # brackets. any of these turns the order into an entry with take profits and a stop,
    # the fields given here win over the ones of the preset
    preset: str | None = None
    take_profits: list[TakeProfit] | None = Field(default=None, max_length=10)
    # the stop, as points from the fill or as an exact price
    stop_loss: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    breakeven_after: int | None = Field(default=None, ge=1)
    breakeven_ticks: int | None = None
    if_open: IfOpen | None = None

    # tradingview sends "buy" but some people write "Buy", so lowercase it
    @field_validator("action", "order_type", "if_open", mode="before")
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

    # true when the alert asks for take profits or a stop
    @property
    def uses_bracket(self):
        fields = (self.preset, self.take_profits, self.stop_loss, self.stop_price,
                  self.breakeven_after, self.breakeven_ticks, self.if_open)
        return any(value is not None for value in fields)

    @model_validator(mode="after")
    def check_fields(self):
        if self.action == "flatten":
            if self.position is not None:
                raise ValueError("flatten does not take a position, use position 0 instead")
            if self.uses_bracket:
                raise ValueError("flatten does not take take profits or a stop")
            return self
        if not self.symbol:
            raise ValueError("symbol is required for buy and sell")
        if self.order_type in ("limit", "stop") and self.price is None:
            raise ValueError("price is required for limit and stop orders")
        if self.position is not None and self.order_type != "market":
            raise ValueError("position only works with market orders")
        if self.uses_bracket:
            # a take profit filling changes the position, the next position alert would buy it back
            if self.position is not None:
                raise ValueError("take profits and stops do not work with position, use qty")
            if self.stop_loss is not None and self.stop_price is not None:
                raise ValueError("send stop_loss or stop_price, not both")
            # with a preset the take profits may come from it, so this is checked again later
            if self.take_profits is not None and self.preset is None:
                check_breakeven(self.take_profits, self.breakeven_after)
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
    # the tradovate order strategy that holds the take profits and stop, for bracket orders
    strategy_id: int | None = None


# what happened for a whole signal (all accounts together)
class SignalResult(Output):
    action: str
    symbol: str | None = None
    contract: str | None = None
    # contracts per account, when the alert asked for a fixed amount
    qty: int | None = None
    # the take profits and stop that were used, like "5 at +8, 5 at +20, stop 12"
    bracket: str | None = None
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

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# base for everything we send back. it makes the api docs say that fields with a
# default (like an empty list) are always present in the answer, so the generated
# angular types do not mark them as optional
class Output(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


# the states a signal can be in (see SignalRecord)
SignalStatus = Literal["pending", "done", "partial", "failed", "dry_run", "duplicate", "error"]
SIGNAL_STATUSES = ["pending", "done", "partial", "failed", "dry_run", "duplicate", "error"]

# where a signal came from. api = a program with an api token, schedule = the daily flatten
SignalSource = Literal["webhook", "dashboard", "api", "schedule"]

# an id chosen by whoever sends the signal or batch. the same id is never run twice
ClientId = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.:-]+$")]


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
    # sending the same client_id again returns the first signal and sends nothing
    client_id: ClientId | None = None

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
    # flatten only: what tradovate shows for the position after closing it (0 = flat)
    net_pos: float | None = None


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
    source: SignalSource
    payload: dict
    # pending = running, done = all accounts worked, partial = some failed, failed = none worked,
    # dry_run = dry run was on, duplicate = ignored, error = broke before any order was sent
    status: SignalStatus
    message: str | None = None
    result: SignalResult | None = None
    client_id: str | None = None
    # the batch this signal was sent in
    batch_id: int | None = None


# ---------- batches: different orders on different accounts, sent together ----------

class BatchBody(BaseModel):
    client_id: ClientId | None = None
    orders: list[Signal] = Field(min_length=1, max_length=50)


class BatchRecord(Output):
    batch_id: int
    client_id: str | None = None
    # pending, done, partial, failed, dry_run or error, worked out from its orders
    status: str
    received_at: str
    # in the order they were sent
    orders: list[SignalRecord] = Field(default_factory=list)


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
    # account names or ids that are never traded or flattened, whatever target_accounts says
    blocked_accounts: list[str] = Field(default_factory=list)
    # account name -> the most contracts it may trade per product, like {"NQ": 1, "MNQ": 10}
    account_limits: dict[str, dict[str, int]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_api_key(self):
        if self.auth_mode == "api_key" and not (self.cid and self.api_secret):
            raise ValueError("api_key mode needs cid and api_secret")
        return self


class TargetsUpdate(BaseModel):
    target_accounts: list[str]


class BlockedUpdate(BaseModel):
    blocked_accounts: list[str]


# one account, by name or id
class AccountChange(BaseModel):
    account: str = Field(min_length=1)


class AccountLimitsUpdate(BaseModel):
    account: str = Field(min_length=1)
    # product -> most contracts, like {"NQ": 1, "MNQ": 10}. empty removes the account's own limits
    max_contracts: dict[str, int] = Field(default_factory=dict)

    @field_validator("max_contracts")
    @classmethod
    def check_limits(cls, value):
        cleaned = {}
        for product, limit in value.items():
            product = product.strip().upper()
            if not product or limit < 0:
                raise ValueError("every limit needs a product and a number of 0 or more")
            cleaned[product] = limit
        return cleaned


class AccountInfo(Output):
    id: int
    name: str
    active: bool
    # true if orders are sent to this account (see target_accounts)
    is_target: bool
    # true if it is never traded or flattened (see blocked_accounts)
    blocked: bool = False
    # showed up after the targets were last chosen, and is not a target
    is_new: bool = False
    # the account's own size limits per product, empty when it uses the server's
    max_contracts: dict[str, int] = Field(default_factory=dict)


class ConnectionHealth(Output):
    label: str
    demo: bool
    auth_mode: str
    authenticated: bool
    user_id: int | None = None
    token_expires_at: str | None = None
    seconds_to_expiry: float | None = None
    accounts: list[AccountInfo] = Field(default_factory=list)
    # every blocked name or id, also of accounts that are not loaded right now
    blocked_accounts: list[str] = Field(default_factory=list)
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


# ---------- fills, balances and risk ----------

# what a fill was: entry, take_profit or stop of a bracket localfill started, flatten, plain
# (a plain localfill order), liquidation (the firm's risk system) or other (placed outside localfill)
FillRole = Literal["entry", "take_profit", "stop", "flatten", "plain", "liquidation", "other"]


class FillRecord(Output):
    # localfill's own number, always growing. read on from the last one you handled
    seq: int
    # tradovate's id of the fill
    fill_id: int
    connection: str
    account_id: int
    account_name: str
    contract: str
    action: Literal["buy", "sell"]
    qty: float
    price: float
    order_id: int | None = None
    strategy_id: int | None = None
    role: FillRole
    # tradovate's time of the fill, and when localfill saw it
    ts: str | None = None
    trade_date: str | None = None
    seen_at: str
    # true for fills that already existed the first time localfill looked (they are not new)
    imported: bool = False
    # every fee tradovate charged for this fill, when it gives them
    fee: float | None = None


# the firm's lock on an account, like the daily loss limit
class AccountRisk(Output):
    # true when tradovate will refuse new orders (liquidate only, trading disabled)
    locked: bool = False
    reason: str | None = None
    # tradovate's own fields, as it sent them
    admin_action: str | None = None
    liquidate_only: str | None = None
    user_triggered_liq_only: bool | None = None


class AccountPosition(Output):
    contract: str
    net_pos: float


class AccountState(Output):
    connection: str
    account_id: int
    name: str
    active: bool
    is_target: bool
    cash_balance: float | None = None
    realized_pnl_today: float | None = None
    open_pnl: float | None = None
    net_liq: float | None = None
    trade_date: str | None = None
    # the fees of today's fills localfill has seen
    fees_today: float | None = None
    positions: list[AccountPosition] = Field(default_factory=list)
    # orders that can still fill
    working_orders: int = 0
    risk: AccountRisk = Field(default_factory=AccountRisk)
    # what could not be read, if anything
    error: str | None = None


class FrontContract(Output):
    symbol: str
    name: str
    id: int


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
    # the size limits: one for every product, and the ones for single products
    max_contracts: int = 0
    max_contracts_per_symbol: dict[str, int] = Field(default_factory=dict)
    # the new york time of the daily flatten, null when it is off
    daily_flatten_et: str | None = None


class SettingsUpdate(BaseModel):
    dry_run: bool | None = None


# one connection, for programs that check localfill before trading
class ConnectionStatus(Output):
    label: str
    online: bool
    # the last time new fills were read without an error, null if never
    last_fill_poll: str | None = None
    last_error: str | None = None


class StatusResponse(Output):
    version: str
    dry_run: bool
    connections_total: int
    connections_online: int
    # true after POST /halt, until POST /resume
    halted: bool = False
    connections: list[ConnectionStatus] = Field(default_factory=list)


class HaltResponse(Output):
    halted: bool
    # the flatten halt ran on every target account
    flatten: SignalRecord | None = None


# ---------- api tokens ----------

class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class ApiToken(Output):
    id: int
    name: str
    created_at: str
    last_used_at: str | None = None


# the only time the token itself is shown
class ApiTokenCreated(ApiToken):
    token: str


class FlattenBody(BaseModel):
    symbol: str | None = None
    connection: str | None = None

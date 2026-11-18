// the json the localfill api sends and receives.
// names are snake_case because that is what the api uses, keep them as they are

export type Action = 'buy' | 'sell' | 'flatten';
export type OrderType = 'market' | 'limit' | 'stop';
// what a bracket does when the account already holds the contract
export type IfOpen = 'reverse' | 'replace' | 'skip';

// pending = received and running, done = every account worked, partial = some failed,
// failed = nothing worked, dry_run = dry run was on, duplicate = ignored,
// error = something broke before any order was sent (for example an unknown symbol)
export type SignalStatus =
  'pending' | 'done' | 'partial' | 'failed' | 'dry_run' | 'duplicate' | 'error';

// where a signal came from. api = a program with an api token, schedule = the daily flatten
export type SignalSource = 'webhook' | 'dashboard' | 'api' | 'schedule';

// ---------- auth ----------

export interface AuthStatus {
  setup_required: boolean;
  logged_in: boolean;
}

// ---------- signals ----------

// what you send to POST /api/signals (same as the tradingview webhook body)
export interface Signal {
  action: Action;
  symbol?: string | null;
  qty?: number;
  order_type?: OrderType;
  price?: number | null;
  contract?: string | null;
  connection?: string | null;
  is_automated?: boolean;
  // target position per account, replaces qty (market orders only)
  position?: number | null;
  // when the alert was sent, late ones are ignored
  sent_at?: string | null;
  // only these account names or ids, out of the target accounts
  accounts?: string[] | null;
  // sending the same client_id again returns the first signal and sends nothing
  client_id?: string | null;
  // brackets: any of these makes it an entry with take profits and a stop
  preset?: string | null;
  take_profits?: TakeProfit[] | null;
  // points from the fill, or an exact price
  stop_loss?: number | null;
  stop_price?: number | null;
  breakeven_after?: number | null;
  breakeven_ticks?: number | null;
  if_open?: IfOpen | null;
}

// ---------- bracket presets ----------

// qty contracts that leave at points from the fill. no points means only the stop
export interface TakeProfit {
  qty: number;
  points?: number | null;
}

// what you send to PUT /api/presets/{name}
export interface PresetConfig {
  take_profits: TakeProfit[];
  // backup stop in points, used when the alert sends none
  stop_loss: number | null;
  // the farthest stop allowed, farther stops from alerts are moved in to it
  max_stop_loss: number | null;
  // once this take profit is reached, the stops of the parts that go further move to the entry
  breakeven_after: number | null;
  // ticks past the entry for that moved stop
  breakeven_ticks: number;
  if_open: IfOpen;
}

export interface Preset extends PresetConfig {
  name: string;
}

// what happened on one tradovate account
export interface AccountResult {
  connection: string;
  account_id: number;
  account_name: string;
  success: boolean;
  latency_ms: number;
  order_id: number | null;
  status: string | null;
  fill_price: number | null;
  error: string | null;
  response: Record<string, unknown> | null;
  // the tradovate order strategy holding the take profits and stop
  strategy_id: number | null;
  // flatten only: the position tradovate shows after closing (0 = flat)
  net_pos: number | null;
}

export interface SignalResult {
  action: string;
  symbol: string | null;
  contract: string | null;
  // contracts per account, when the alert asked for a fixed amount
  qty: number | null;
  // the take profits and stop used, like "5 at +8, 5 at +20, stop 12"
  bracket: string | null;
  total: number;
  succeeded: number;
  failed: number;
  results: AccountResult[];
  warnings: string[];
}

// one row of the history
export interface SignalRecord {
  id: number;
  received_at: string;
  source: SignalSource;
  // the signal as we received it (cleaned up). partial because old rows can miss newer fields
  payload: Partial<Signal>;
  status: SignalStatus;
  message: string | null;
  result: SignalResult | null;
  client_id: string | null;
  // the batch it was sent in
  batch_id: number | null;
}

// ---------- batches ----------

// what you send to POST /api/batches. every order needs accounts
export interface BatchBody {
  client_id?: string | null;
  orders: Signal[];
}

export interface BatchRecord {
  batch_id: number;
  client_id: string | null;
  status: string;
  received_at: string;
  orders: SignalRecord[];
}

// how many signals are in each state, for the tabs above the history table
export interface SignalCounts {
  total: number;
  pending: number;
  done: number;
  partial: number;
  failed: number;
  dry_run: number;
  duplicate: number;
  error: number;
}

// ---------- connections ----------

// what you send to POST /api/connections
export interface ConnectionConfig {
  label: string;
  username: string;
  password: string;
  demo?: boolean;
  auth_mode?: 'web' | 'api_key';
  cid?: string | null;
  api_secret?: string | null;
  target_accounts?: string[];
}

export interface AccountInfo {
  id: number;
  name: string;
  active: boolean;
  is_target: boolean;
  // never traded or flattened, whatever the targets say
  blocked: boolean;
  // showed up after the targets were last chosen, and is not a target
  is_new: boolean;
  // the account's own size limits per product, empty when it uses the server's
  max_contracts: Record<string, number>;
}

export interface ConnectionHealth {
  label: string;
  demo: boolean;
  auth_mode: string;
  authenticated: boolean;
  user_id: number | null;
  token_expires_at: string | null;
  seconds_to_expiry: number | null;
  accounts: AccountInfo[];
  // every blocked name or id, also of accounts that are not loaded right now
  blocked_accounts: string[];
  last_error: string | null;
}

// ---------- positions and pnl ----------

export interface OpenPosition {
  connection: string;
  account_id: number;
  account_name: string;
  contract_id: number;
  contract_name: string;
  net_pos: number;
}

export interface PositionsResponse {
  positions: OpenPosition[];
  // more than one name means you are in two contract months at once
  distinct_contracts: string[];
}

export interface FillPair {
  buy_price: number;
  sell_price: number;
  qty: number;
  ts: string | null;
}

export interface AccountPnl {
  connection: string;
  account_id: number;
  account_name: string;
  realized_pnl: number | null;
  trade_date: string | null;
  fill_pairs: FillPair[];
}

export interface PnlResponse {
  accounts: AccountPnl[];
}

// ---------- fills, balances and risk ----------

export type FillRole =
  'entry' | 'take_profit' | 'stop' | 'flatten' | 'plain' | 'liquidation' | 'other';

export interface FillRecord {
  // localfill's own number, always growing
  seq: number;
  fill_id: number;
  connection: string;
  account_id: number;
  account_name: string;
  contract: string;
  action: 'buy' | 'sell';
  qty: number;
  price: number;
  order_id: number | null;
  strategy_id: number | null;
  role: FillRole;
  ts: string | null;
  trade_date: string | null;
  seen_at: string;
  // already there the first time localfill looked
  imported: boolean;
  fee: number | null;
}

// the firm's lock on an account, like the daily loss limit
export interface AccountRisk {
  locked: boolean;
  reason: string | null;
  admin_action: string | null;
  liquidate_only: string | null;
  user_triggered_liq_only: boolean | null;
}

export interface AccountState {
  connection: string;
  account_id: number;
  name: string;
  active: boolean;
  is_target: boolean;
  cash_balance: number | null;
  realized_pnl_today: number | null;
  open_pnl: number | null;
  net_liq: number | null;
  trade_date: string | null;
  fees_today: number | null;
  positions: { contract: string; net_pos: number }[];
  working_orders: number;
  risk: AccountRisk;
  error: string | null;
}

// ---------- api tokens ----------

export interface ApiToken {
  id: number;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

// the only time the token itself is shown
export interface ApiTokenCreated extends ApiToken {
  token: string;
}

// ---------- settings and status ----------

export interface Settings {
  dry_run: boolean;
  webhook_token: string;
  webhook_path: string;
  // null unless LOCALFILL_PUBLIC_URL is set
  webhook_url: string | null;
  // the size limits: one for every product, and the ones for single products
  max_contracts: number;
  max_contracts_per_symbol: Record<string, number>;
  // new york time of the daily flatten, null when it is off
  daily_flatten_et: string | null;
}

export interface ConnectionStatus {
  label: string;
  online: boolean;
  last_fill_poll: string | null;
  last_error: string | null;
}

export interface Status {
  version: string;
  dry_run: boolean;
  connections_total: number;
  connections_online: number;
  // true after a halt, until resume
  halted: boolean;
  connections: ConnectionStatus[];
}

export interface HaltResponse {
  halted: boolean;
  flatten: SignalRecord | null;
}

// a change to one account, from the "accounts" event
export interface AccountChange {
  connection: string;
  account_id: number;
  name: string;
  active: boolean;
  change: 'new' | 'closed' | 'locked' | 'unlocked';
  reason?: string | null;
}

export interface PositionChange {
  connection: string;
  account_id: number;
  account_name: string;
  contract: string;
  net_pos: number;
}

// ---------- live events (GET /api/events) ----------

export type SseEvent =
  | { type: 'signal'; data: SignalRecord }
  | { type: 'connections'; data: null }
  | { type: 'settings'; data: null }
  | { type: 'presets'; data: null }
  | { type: 'fill'; data: FillRecord }
  | { type: 'position'; data: PositionChange }
  | { type: 'accounts'; data: AccountChange };

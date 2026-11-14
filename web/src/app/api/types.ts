// the json the localfill api sends and receives.
// names are snake_case because that is what the api uses, keep them as they are

export type Action = 'buy' | 'sell' | 'flatten';
export type OrderType = 'market' | 'limit' | 'stop';
// what a bracket does when the account already holds the contract
export type IfOpen = 'reverse' | 'replace' | 'skip';

// pending = received and running, done = every account worked, partial = some failed,
// failed = nothing worked, dry_run = dry run was on, duplicate = ignored,
// error = something broke before any order was sent (for example an unknown symbol)
export type SignalStatus = 'pending' | 'done' | 'partial' | 'failed' | 'dry_run' | 'duplicate' | 'error';

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
  source: 'webhook' | 'dashboard';
  // the signal as we received it (cleaned up). partial because old rows can miss newer fields
  payload: Partial<Signal>;
  status: SignalStatus;
  message: string | null;
  result: SignalResult | null;
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

// ---------- settings and status ----------

export interface Settings {
  dry_run: boolean;
  webhook_token: string;
  webhook_path: string;
  // null unless LOCALFILL_PUBLIC_URL is set
  webhook_url: string | null;
}

export interface Status {
  version: string;
  dry_run: boolean;
  connections_total: number;
  connections_online: number;
}

// ---------- live events (GET /api/events) ----------

export type SseEvent =
  | { type: 'signal'; data: SignalRecord }
  | { type: 'connections'; data: null }
  | { type: 'settings'; data: null }
  | { type: 'presets'; data: null };

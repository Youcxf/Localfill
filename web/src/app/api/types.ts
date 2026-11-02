// the json the localfill api sends and receives.
// names are snake_case because that is what the api uses, keep them as they are

export type Action = 'buy' | 'sell' | 'flatten';
export type OrderType = 'market' | 'limit' | 'stop';

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
}

export interface SignalResult {
  action: string;
  symbol: string | null;
  contract: string | null;
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
  | { type: 'settings'; data: null };

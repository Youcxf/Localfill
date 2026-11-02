import { AccountResult, SignalRecord, SignalStatus } from '../../api/types';
import { formatPrice, utcDateTime, utcTime } from '../../core/format';

// turns a signal from the api into the words and numbers the screens show.
// it lives here, away from the components, so it is easy to read and to test

// ---------- one row of the history table ----------

export interface SignalRow {
  id: number;
  time: string;
  source: string;
  action: string;
  actionColor: 'green' | 'red' | 'amber';
  detail: string;
  contract: string;
  message: string;
  accounts: string;
  fill: string;
  status: SignalStatus;
}

export function toRow(record: SignalRecord): SignalRow {
  const action = (record.payload.action ?? '').toUpperCase();
  return {
    id: record.id,
    time: utcTime(record.received_at),
    source: record.source,
    action,
    actionColor: action === 'BUY' ? 'green' : action === 'SELL' ? 'red' : 'amber',
    detail: describe(record),
    contract: record.result?.contract ?? record.payload.contract ?? '',
    message: record.message ?? '',
    accounts: record.result ? `${record.result.succeeded}/${record.result.total}` : '—',
    fill: fillText(record),
    status: record.status,
  };
}

// "2 MNQ", "1 MNQ limit 19,980.00", or "MNQ" for a flatten
export function describe(record: SignalRecord): string {
  const payload = record.payload;
  const symbol = payload.symbol ?? '';
  if (payload.action === 'flatten') {
    return symbol || 'all positions';
  }
  let text = `${payload.qty ?? 1} ${symbol}`;
  if (payload.order_type && payload.order_type !== 'market' && payload.price != null) {
    text += ` ${payload.order_type} ${formatPrice(payload.price)}`;
  }
  return text;
}

// the "fill" column: the price we got, or how many positions were closed
function fillText(record: SignalRecord): string {
  const result = record.result;
  if (!result) {
    return '—';
  }
  if (result.action === 'flatten') {
    const closed = result.results.filter((r) => r.status?.startsWith('closed')).length;
    return closed ? `${closed} closed` : 'nothing to close';
  }
  const filled = result.results.find((r) => r.fill_price !== null);
  if (filled?.fill_price != null) {
    return formatPrice(filled.fill_price);
  }
  return result.results.find((r) => r.status)?.status ?? '—';
}

// ---------- the detail panel ----------

export interface AccountLine {
  account: string;
  connection: string;
  latency: string;
  outcome: string;
  detail: string;
  color: 'green' | 'red';
}

export interface SignalDetail {
  id: number;
  status: SignalStatus;
  when: string;
  source: string;
  request: { k: string; v: string }[];
  message: string;
  warnings: string[];
  resultTitle: string;
  results: AccountLine[];
  // tradovate's raw answers as text, for people who want to look closer
  raw: string | null;
}

export function toDetail(record: SignalRecord): SignalDetail {
  const payload = record.payload;
  const result = record.result;

  const request = [
    { k: 'action', v: payload.action ?? '' },
    { k: 'symbol', v: payload.symbol ?? 'all' },
    { k: 'contract', v: result?.contract ?? payload.contract ?? '—' },
  ];
  if (payload.action !== 'flatten') {
    request.push({ k: 'qty', v: String(payload.qty ?? 1) });
    request.push({ k: 'order type', v: payload.order_type ?? 'market' });
    if (payload.price != null) {
      request.push({ k: 'price', v: formatPrice(payload.price) });
    }
  }
  request.push({ k: 'connection', v: payload.connection ?? 'all' });

  return {
    id: record.id,
    status: record.status,
    when: utcDateTime(record.received_at),
    source: record.source,
    request,
    message: record.message ?? '',
    warnings: result?.warnings ?? [],
    resultTitle: result ? `Accounts, ${result.succeeded} of ${result.total} succeeded` : '',
    results: (result?.results ?? []).map(toAccountLine),
    raw: result?.results.length ? JSON.stringify(result.results.map((r) => r.response), null, 2) : null,
  };
}

function toAccountLine(r: AccountResult): AccountLine {
  const outcome = (r.status ?? (r.success ? 'ok' : 'failed')).toLowerCase();

  // "at 20,055.50 · order 88214"
  const parts: string[] = [];
  if (r.fill_price != null) {
    parts.push(`at ${formatPrice(r.fill_price)}`);
  }
  if (r.order_id != null) {
    parts.push(`order ${r.order_id}`);
  }
  // the reason, unless the outcome word already says it
  if (!r.success && r.error && !r.error.toLowerCase().includes(outcome)) {
    parts.push(r.error);
  }

  return {
    account: r.account_name,
    connection: r.connection,
    latency: `${Math.round(r.latency_ms)} ms`,
    outcome,
    detail: parts.join(' · '),
    color: r.success ? 'green' : 'red',
  };
}

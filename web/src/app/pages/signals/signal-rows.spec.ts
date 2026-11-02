import { AccountResult, SignalRecord, SignalResult } from '../../api/types';
import { describe as describeSignal, toDetail, toRow } from './signal-rows';

// one account result, tests change only what they care about
function account(changes: Partial<AccountResult> = {}): AccountResult {
  return {
    connection: 'Prop 1',
    account_id: 1001,
    account_name: 'DEMO1001',
    success: true,
    latency_ms: 141.6,
    order_id: 88214,
    status: 'Filled',
    fill_price: 20055.5,
    error: null,
    response: { orderId: 88214 },
    ...changes,
  };
}

function result(results: AccountResult[], changes: Partial<SignalResult> = {}): SignalResult {
  const succeeded = results.filter((r) => r.success).length;
  return {
    action: 'buy',
    symbol: 'MNQ',
    contract: 'MNQZ6',
    total: results.length,
    succeeded,
    failed: results.length - succeeded,
    results,
    warnings: [],
    ...changes,
  };
}

function record(changes: Partial<SignalRecord> = {}): SignalRecord {
  return {
    id: 7,
    received_at: '2026-09-20T21:46:57+00:00',
    source: 'webhook',
    payload: { action: 'buy', symbol: 'MNQ', qty: 2 },
    status: 'done',
    message: null,
    result: result([account(), account({ account_id: 1002, account_name: 'DEMO1002' })]),
    ...changes,
  };
}

describe('signal rows', () => {
  it('describes a market order', () => {
    expect(describeSignal(record())).toBe('2 MNQ');
  });

  it('adds the order type and price for a limit order', () => {
    const limit = record({ payload: { action: 'buy', symbol: 'MNQ', qty: 1, order_type: 'limit', price: 19980 } });
    expect(describeSignal(limit)).toBe('1 MNQ limit 19,980.00');
  });

  it('describes a flatten by its symbol, or as all positions', () => {
    expect(describeSignal(record({ payload: { action: 'flatten', symbol: 'ES' } }))).toBe('ES');
    expect(describeSignal(record({ payload: { action: 'flatten' } }))).toBe('all positions');
  });

  it('builds a table row from a filled signal', () => {
    const row = toRow(record());
    expect(row.time).toBe('21:46:57');
    expect(row.action).toBe('BUY');
    expect(row.actionColor).toBe('green');
    expect(row.contract).toBe('MNQZ6');
    expect(row.accounts).toBe('2/2');
    expect(row.fill).toBe('20,055.50');
  });

  it('colors sells red and flattens amber', () => {
    expect(toRow(record({ payload: { action: 'sell', symbol: 'ES' } })).actionColor).toBe('red');
    expect(toRow(record({ payload: { action: 'flatten' } })).actionColor).toBe('amber');
  });

  it('shows dashes while there is no result yet', () => {
    const row = toRow(record({ status: 'pending', result: null }));
    expect(row.accounts).toBe('—');
    expect(row.fill).toBe('—');
  });

  it('counts closed positions for a flatten', () => {
    const closed = account({ status: 'closed', fill_price: null });
    const nothing = account({ status: 'no position', fill_price: null });
    const flatten = record({
      payload: { action: 'flatten', symbol: 'ES' },
      result: result([closed, closed, nothing], { action: 'flatten' }),
    });
    expect(toRow(flatten).fill).toBe('2 closed');

    const empty = record({ payload: { action: 'flatten' }, result: result([nothing], { action: 'flatten' }) });
    expect(toRow(empty).fill).toBe('nothing to close');
  });

  it('shows the status word when nothing filled', () => {
    const working = record({ result: result([account({ status: 'Working', fill_price: null })]) });
    expect(toRow(working).fill).toBe('Working');
  });
});

describe('signal detail', () => {
  it('lists the request the way it was sent', () => {
    const detail = toDetail(record());
    expect(detail.when).toBe('2026-09-20 21:46:57 UTC');
    expect(detail.request).toEqual([
      { k: 'action', v: 'buy' },
      { k: 'symbol', v: 'MNQ' },
      { k: 'contract', v: 'MNQZ6' },
      { k: 'qty', v: '2' },
      { k: 'order type', v: 'market' },
      { k: 'connection', v: 'all' },
    ]);
  });

  it('has no qty or order type for a flatten', () => {
    const detail = toDetail(record({ payload: { action: 'flatten' } }));
    expect(detail.request.map((line) => line.k)).toEqual(['action', 'symbol', 'contract', 'connection']);
  });

  it('writes one line per account', () => {
    const detail = toDetail(record());
    expect(detail.resultTitle).toBe('Accounts, 2 of 2 succeeded');
    expect(detail.results[0]).toEqual({
      account: 'DEMO1001',
      connection: 'Prop 1',
      latency: '142 ms',
      outcome: 'filled',
      detail: 'at 20,055.50 · order 88214',
      color: 'green',
    });
  });

  it('adds the reason when an account failed', () => {
    const failed = account({ success: false, status: null, order_id: null, fill_price: null, error: 'no margin' });
    const line = toDetail(record({ result: result([failed]) })).results[0];
    expect(line.outcome).toBe('failed');
    expect(line.detail).toBe('no margin');
    expect(line.color).toBe('red');
  });

  it('does not repeat the reason when the outcome already says it', () => {
    const failed = account({ success: false, status: 'Rejected', error: 'Rejected', fill_price: null, order_id: null });
    expect(toDetail(record({ result: result([failed]) })).results[0].detail).toBe('');
  });

  it('keeps tradovate raw answers as text, or null without any', () => {
    expect(toDetail(record()).raw).toContain('"orderId": 88214');
    expect(toDetail(record({ result: null })).raw).toBeNull();
  });
});

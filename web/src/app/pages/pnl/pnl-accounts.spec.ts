import { AccountPnl, FillPair, PnlResponse } from '../../api/types';
import { toAccounts, toTrip, total } from './pnl-accounts';

function pnl(name: string, realized: number | null, pairs: FillPair[] = []): AccountPnl {
  return {
    connection: 'Prop 1',
    account_id: Number(name.slice(-4)),
    account_name: name,
    realized_pnl: realized,
    trade_date: '2026-09-21',
    fill_pairs: pairs,
  };
}

describe('pnl accounts', () => {
  it('copies what the api sends and builds a key per account', () => {
    const response: PnlResponse = { accounts: [pnl('DEMO1001', 12.5)] };
    const [account] = toAccounts(response);
    expect(account.key).toBe('Prop 1/1001');
    expect(account.name).toBe('DEMO1001');
    expect(account.pnl).toBe(12.5);
    expect(account.tradeDate).toBe('2026-09-21');
  });

  it('adds up the accounts that have a number', () => {
    const accounts = toAccounts({ accounts: [pnl('DEMO1001', 100), pnl('DEMO1002', -40.5), pnl('DEMO1003', null)] });
    expect(total(accounts)).toBe(59.5);
  });

  it('gives null when no account has a number yet', () => {
    expect(total(toAccounts({ accounts: [pnl('DEMO1001', null)] }))).toBeNull();
    expect(total([])).toBeNull();
  });
});

describe('round trips', () => {
  it('shows the points between the buy and the sell', () => {
    const trip = toTrip({ buy_price: 20004.75, sell_price: 20007.75, qty: 4, ts: '2026-09-21T14:27:26+00:00' });
    expect(trip).toEqual({
      time: '14:27:26',
      buy: '20,004.75',
      sell: '20,007.75',
      qty: 4,
      points: '+3.00',
      color: 'green',
    });
  });

  it('turns red when the sell was lower than the buy', () => {
    const trip = toTrip({ buy_price: 6005.75, sell_price: 6005.5, qty: 2, ts: null });
    expect(trip.points).toBe('−0.25');
    expect(trip.color).toBe('red');
    expect(trip.time).toBe('');
  });
});

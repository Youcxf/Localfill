import { FillPair, PnlResponse } from '../../api/types';
import { formatPrice, formatSigned, utcTime } from '../../core/format';

// the numbers on the p/l screen, worked out from what /api/pnl sends

export interface PnlAccount {
  key: string;
  connection: string;
  name: string;
  // realized p/l in account currency, straight from tradovate. null when tradovate did not say
  pnl: number | null;
  tradeDate: string | null;
  pairs: FillPair[];
}

export interface Trip {
  time: string;
  buy: string;
  sell: string;
  qty: number;
  points: string;
  color: 'green' | 'red';
}

export function toAccounts(response: PnlResponse): PnlAccount[] {
  return response.accounts.map((a) => ({
    key: `${a.connection}/${a.account_id}`,
    connection: a.connection,
    name: a.account_name,
    pnl: a.realized_pnl,
    tradeDate: a.trade_date,
    pairs: a.fill_pairs,
  }));
}

// the total of all accounts, or null when no account has a number yet
export function total(accounts: PnlAccount[]): number | null {
  const known = accounts.filter((a) => a.pnl !== null);
  return known.length ? known.reduce((sum, a) => sum + (a.pnl ?? 0), 0) : null;
}

// one line of the round trips table
export function toTrip(pair: FillPair): Trip {
  const points = pair.sell_price - pair.buy_price;
  return {
    time: utcTime(pair.ts),
    buy: formatPrice(pair.buy_price),
    sell: formatPrice(pair.sell_price),
    qty: pair.qty,
    points: formatSigned(points, 2),
    color: points < 0 ? 'red' : 'green',
  };
}

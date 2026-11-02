import { OpenPosition } from '../../api/types';
import { formatSigned } from '../../core/format';

// positions grouped by contract, because the flatten button works on a whole contract

export interface PositionRow {
  connection: string;
  account: string;
  qty: string;
  side: 'long' | 'short';
}

export interface PositionGroup {
  contract: string;
  net: number;
  summary: string;
  // true when the same contract is also open in a later month (a rollover left it behind)
  old: boolean;
  accounts: number;
  rows: PositionRow[];
}

// futures month codes, january to december
const MONTHS = 'FGHJKMNQUVXZ';

// "MNQZ6" -> root "MNQ", month Z (december), year digit 6
export function parseContract(name: string): { root: string; expiry: number } | null {
  const match = /^(.+?)([FGHJKMNQUVXZ])(\d)$/.exec(name);
  if (!match) {
    return null;
  }
  return { root: match[1], expiry: Number(match[3]) * 12 + MONTHS.indexOf(match[2]) };
}

// is contract a expiring after contract b (same root)? the year is one digit, so 9 comes before 0
function expiresAfter(a: string, b: string): boolean {
  const first = parseContract(a);
  const second = parseContract(b);
  if (!first || !second || first.root !== second.root) {
    return false;
  }
  const gap = first.expiry - second.expiry;
  return Math.abs(gap) > 60 ? gap < 0 : gap > 0;
}

export function groupPositions(positions: OpenPosition[]): PositionGroup[] {
  const byContract = new Map<string, OpenPosition[]>();
  for (const position of positions) {
    byContract.set(position.contract_name, [...(byContract.get(position.contract_name) ?? []), position]);
  }

  const groups: PositionGroup[] = [];
  for (const [contract, items] of byContract) {
    const net = items.reduce((sum, p) => sum + p.net_pos, 0);
    const accounts = new Set(items.map((p) => `${p.connection}/${p.account_id}`)).size;
    groups.push({
      contract,
      net,
      summary: `net ${formatSigned(net)} ${accounts === 1 ? 'on 1 account' : `across ${accounts} accounts`}`,
      old: false,
      accounts,
      rows: items.map((p) => ({
        connection: p.connection,
        account: p.account_name,
        qty: formatSigned(p.net_pos),
        side: p.net_pos < 0 ? 'short' : 'long',
      })),
    });
  }

  for (const group of groups) {
    group.old = groups.some((other) => expiresAfter(other.contract, group.contract));
  }
  return groups;
}

const NUMBER_WORDS = ['', '', 'two', 'three', 'four', 'five'];

// "MNQ is open in two contract months, MNQZ6 and MNQU6. Close the old month before it expires."
export function rolloverNotice(groups: PositionGroup[]): string {
  const byRoot = new Map<string, string[]>();
  for (const group of groups) {
    const parsed = parseContract(group.contract);
    if (parsed) {
      byRoot.set(parsed.root, [...(byRoot.get(parsed.root) ?? []), group.contract]);
    }
  }

  const sentences: string[] = [];
  for (const [root, names] of byRoot) {
    if (names.length > 1) {
      const how = NUMBER_WORDS[names.length] || String(names.length);
      const list = names.length === 2 ? names.join(' and ') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
      sentences.push(`${root} is open in ${how} contract months, ${list}.`);
    }
  }
  return sentences.length ? `${sentences.join(' ')} Close the old month before it expires.` : '';
}

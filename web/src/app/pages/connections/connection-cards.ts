import { AccountInfo, ConnectionHealth } from '../../api/types';
import { formatDuration } from '../../core/format';

// what one login shows on the connections screen

export interface ConnectionCard {
  label: string;
  env: 'DEMO' | 'LIVE';
  mode: string;
  online: boolean;
  status: string;
  note: string;
  // the last login problem, empty when there is none
  error: string;
  accounts: AccountInfo[];
}

export function toCard(c: ConnectionHealth): ConnectionCard {
  let note = 'not logged in';
  if (c.authenticated) {
    note = 'token renews by itself';
    if (c.seconds_to_expiry != null) {
      note += `, expires in ${formatDuration(c.seconds_to_expiry)}`;
    }
  } else if (c.last_error) {
    note = 'could not log in';
  }

  return {
    label: c.label,
    env: c.demo ? 'DEMO' : 'LIVE',
    mode: c.auth_mode === 'api_key' ? 'api key' : 'web login',
    online: c.authenticated,
    status: c.authenticated ? 'Logged in' : 'Offline',
    note,
    error: c.last_error ?? '',
    accounts: c.accounts,
  };
}

// "3 logins, 4 accounts, 3 receiving orders"
export function summarize(connections: ConnectionHealth[]): string {
  const accounts = connections.reduce((total, c) => total + c.accounts.length, 0);
  const receiving = connections.reduce((total, c) => total + c.accounts.filter((a) => a.is_target).length, 0);
  return `${count(connections.length, 'login')}, ${count(accounts, 'account')}, ${receiving} receiving orders`;
}

function count(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

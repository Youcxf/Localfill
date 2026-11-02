import { ConnectionHealth } from '../../api/types';
import { summarize, toCard } from './connection-cards';

function connection(changes: Partial<ConnectionHealth> = {}): ConnectionHealth {
  return {
    label: 'Prop 1',
    demo: true,
    auth_mode: 'web',
    authenticated: true,
    user_id: 1,
    token_expires_at: null,
    seconds_to_expiry: 4465,
    accounts: [
      { id: 1001, name: 'DEMO1001', active: true, is_target: true },
      { id: 1002, name: 'DEMO1002', active: true, is_target: false },
    ],
    last_error: null,
    ...changes,
  };
}

describe('connection card', () => {
  it('shows a logged in connection with its time left', () => {
    const card = toCard(connection());
    expect(card.env).toBe('DEMO');
    expect(card.mode).toBe('web login');
    expect(card.online).toBe(true);
    expect(card.status).toBe('Logged in');
    expect(card.note).toBe('token renews by itself, expires in 1 h 14 min');
  });

  it('leaves the time out when the api does not know it', () => {
    expect(toCard(connection({ seconds_to_expiry: null })).note).toBe('token renews by itself');
  });

  it('marks live accounts and api key logins', () => {
    const card = toCard(connection({ demo: false, auth_mode: 'api_key' }));
    expect(card.env).toBe('LIVE');
    expect(card.mode).toBe('api key');
  });

  it('says offline and keeps the error when the login failed', () => {
    const card = toCard(connection({ authenticated: false, last_error: 'login rejected' }));
    expect(card.online).toBe(false);
    expect(card.status).toBe('Offline');
    expect(card.note).toBe('could not log in');
    expect(card.error).toBe('login rejected');
  });

  it('says not logged in when there is no error yet', () => {
    expect(toCard(connection({ authenticated: false })).note).toBe('not logged in');
  });
});

describe('summary line', () => {
  it('counts logins, accounts and the accounts that receive orders', () => {
    expect(summarize([connection(), connection({ label: 'Prop 2' })])).toBe('2 logins, 4 accounts, 2 receiving orders');
  });

  it('uses the singular for one login and one account', () => {
    const one = connection({ accounts: [{ id: 1, name: 'DEMO1', active: true, is_target: true }] });
    expect(summarize([one])).toBe('1 login, 1 account, 1 receiving orders');
  });

  it('works with no connections', () => {
    expect(summarize([])).toBe('0 logins, 0 accounts, 0 receiving orders');
  });
});

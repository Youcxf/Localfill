import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import {
  AccountState,
  ApiToken,
  ApiTokenCreated,
  AuthStatus,
  ConnectionConfig,
  ConnectionHealth,
  HaltResponse,
  PnlResponse,
  PositionsResponse,
  Preset,
  PresetConfig,
  Settings,
  Signal,
  SignalCounts,
  SignalRecord,
  SignalStatus,
  Status,
} from './types';

// every call to the localfill api lives here, so components never build urls themselves.
// the urls start with /api. while developing, the proxy sends them to port 8011
@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  // no login needed, we use it to show the version on the login page
  health() {
    return this.http.get<{ status: string; version: string }>('/api/health');
  }

  // ---------- auth ----------

  // ask this first when the app starts, it tells you which screen to show
  authStatus() {
    return this.http.get<AuthStatus>('/api/auth/status');
  }

  // first run only
  setup(password: string) {
    return this.http.post<AuthStatus>('/api/auth/setup', { password });
  }

  login(password: string) {
    return this.http.post<AuthStatus>('/api/auth/login', { password });
  }

  logout() {
    return this.http.post<void>('/api/auth/logout', {});
  }

  // ---------- status and settings ----------

  status() {
    return this.http.get<Status>('/api/status');
  }

  settings() {
    return this.http.get<Settings>('/api/settings');
  }

  setDryRun(dryRun: boolean) {
    return this.http.patch<Settings>('/api/settings', { dry_run: dryRun });
  }

  // makes a new webhook url, the old one stops working
  regenerateWebhookToken() {
    return this.http.post<Settings>('/api/settings/webhook-token/regenerate', {});
  }

  // ---------- api tokens, for programs like a trading bot ----------

  tokens() {
    return this.http.get<ApiToken[]>('/api/tokens');
  }

  // the answer is the only time the token is shown
  createToken(name: string) {
    return this.http.post<ApiTokenCreated>('/api/tokens', { name });
  }

  revokeToken(id: number) {
    return this.http.delete<void>('/api/tokens/' + id);
  }

  // ---------- the kill switch ----------

  // flattens every target account now and refuses new buys and sells until resume
  halt() {
    return this.http.post<HaltResponse>('/api/halt', {});
  }

  resume() {
    return this.http.post<HaltResponse>('/api/resume', {});
  }

  // ---------- connections ----------

  connections() {
    return this.http.get<ConnectionHealth[]>('/api/connections');
  }

  // logs in to tradovate first, so this can take a few seconds. a wrong password gives a 400
  addConnection(config: ConnectionConfig) {
    return this.http.post<ConnectionHealth>('/api/connections', config);
  }

  // choose which accounts get orders (an empty list means all of them)
  setTargets(label: string, targetAccounts: string[]) {
    return this.http.patch<ConnectionHealth>(this.connectionUrl(label), { target_accounts: targetAccounts });
  }

  reconnect(label: string) {
    return this.http.post<ConnectionHealth>(this.connectionUrl(label) + '/reconnect', {});
  }

  // reloads the account list, so an account you just bought shows up
  refreshAccounts(label: string) {
    return this.http.post<ConnectionHealth>(this.connectionUrl(label) + '/refresh', {});
  }

  // accounts that are never traded or flattened (the full list)
  setBlocked(label: string, blocked: string[]) {
    return this.http.put<ConnectionHealth>(this.connectionUrl(label) + '/blocked', {
      blocked_accounts: blocked,
    });
  }

  // one account's own size limits per product, like { NQ: 1, MNQ: 10 }. empty removes them
  setLimits(label: string, account: string, maxContracts: Record<string, number>) {
    return this.http.put<ConnectionHealth>(this.connectionUrl(label) + '/limits', {
      account,
      max_contracts: maxContracts,
    });
  }

  deleteConnection(label: string) {
    return this.http.delete<void>(this.connectionUrl(label));
  }

  // labels can hold spaces, so always encode them
  private connectionUrl(label: string) {
    return '/api/connections/' + encodeURIComponent(label);
  }

  // ---------- bracket presets ----------

  presets() {
    return this.http.get<Preset[]>('/api/presets');
  }

  // creates the preset or replaces the one with this name
  savePreset(name: string, config: PresetConfig) {
    return this.http.put<Preset>('/api/presets/' + encodeURIComponent(name), config);
  }

  deletePreset(name: string) {
    return this.http.delete<void>('/api/presets/' + encodeURIComponent(name));
  }

  // ---------- trading ----------

  positions() {
    return this.http.get<PositionsResponse>('/api/positions');
  }

  pnl() {
    return this.http.get<PnlResponse>('/api/pnl');
  }

  // real balances, positions and the firm's lock, for every target account
  accounts(all = false) {
    return this.http.get<AccountState[]>('/api/accounts', { params: { all } });
  }

  // newest first. give a status to only get signals in that state
  signals(limit = 50, offset = 0, status?: SignalStatus) {
    const params: Record<string, string | number> = { limit, offset };
    if (status) {
      params['status'] = status;
    }
    return this.http.get<SignalRecord[]>('/api/signals', { params });
  }

  // how many signals are in each state
  signalCounts() {
    return this.http.get<SignalCounts>('/api/signals/counts');
  }

  signal(id: number) {
    return this.http.get<SignalRecord>('/api/signals/' + id);
  }

  // sends an order from the dashboard. it waits until tradovate answered, so show a spinner
  sendSignal(signal: Signal) {
    return this.http.post<SignalRecord>('/api/signals', signal);
  }

  // the panic button. give a symbol to close only that one
  flatten(symbol?: string, connection?: string) {
    return this.http.post<SignalRecord>('/api/flatten', { symbol, connection });
  }
}

import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import {
  AuthStatus,
  ConnectionConfig,
  ConnectionHealth,
  PnlResponse,
  PositionsResponse,
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

  deleteConnection(label: string) {
    return this.http.delete<void>(this.connectionUrl(label));
  }

  // labels can hold spaces, so always encode them
  private connectionUrl(label: string) {
    return '/api/connections/' + encodeURIComponent(label);
  }

  // ---------- trading ----------

  positions() {
    return this.http.get<PositionsResponse>('/api/positions');
  }

  pnl() {
    return this.http.get<PnlResponse>('/api/pnl');
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

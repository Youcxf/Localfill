import { Routes } from '@angular/router';
import { authGuard } from './api/auth';
import { Shell } from './layout/shell';
import { ConnectionsPage } from './pages/connections/connections-page';
import { LoginPage } from './pages/login/login-page';
import { OrderPage } from './pages/order/order-page';
import { PnlPage } from './pages/pnl/pnl-page';
import { PositionsPage } from './pages/positions/positions-page';
import { SettingsPage } from './pages/settings/settings-page';
import { SetupPage } from './pages/setup/setup-page';
import { SignalsPage } from './pages/signals/signals-page';

// every screen after login lives inside the shell, and needs the login (authGuard).
// data.title is what the top bar shows, title is what the browser tab shows
export const routes: Routes = [
  { path: 'login', component: LoginPage, title: 'Sign in · localfill' },
  { path: 'setup', component: SetupPage, title: 'Welcome · localfill' },
  {
    path: '',
    component: Shell,
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'signals' },
      { path: 'signals', component: SignalsPage, title: 'Signals · localfill', data: { title: 'Signals' } },
      { path: 'connections', component: ConnectionsPage, title: 'Connections · localfill', data: { title: 'Connections' } },
      { path: 'positions', component: PositionsPage, title: 'Positions · localfill', data: { title: 'Positions' } },
      { path: 'pnl', component: PnlPage, title: 'P/L · localfill', data: { title: 'P/L' } },
      { path: 'order', component: OrderPage, title: 'Manual order · localfill', data: { title: 'Manual order' } },
      { path: 'settings', component: SettingsPage, title: 'Settings · localfill', data: { title: 'Settings' } },
    ],
  },
  { path: '**', redirectTo: '' },
];

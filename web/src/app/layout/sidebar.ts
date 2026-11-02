import { Component, computed, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';
import { ApiService } from '../api/api.service';
import { AppState } from '../core/app-state';
import { Logo } from '../shared/logo';

@Component({
  selector: 'app-sidebar',
  imports: [RouterLink, RouterLinkActive, Logo],
  templateUrl: './sidebar.html',
  styleUrl: './sidebar.css',
})
export class Sidebar {
  private api = inject(ApiService);
  private router = inject(Router);
  private state = inject(AppState);

  protected readonly items = [
    { path: '/signals', label: 'Signals' },
    { path: '/connections', label: 'Connections' },
    { path: '/positions', label: 'Positions' },
    { path: '/pnl', label: 'P/L' },
    { path: '/order', label: 'Manual order' },
    { path: '/settings', label: 'Settings' },
  ];

  protected readonly version = computed(() => this.state.status()?.version ?? '');

  // "2 of 3 connections online"
  protected readonly online = computed(() => {
    const status = this.state.status();
    if (!status) {
      return '';
    }
    return `${status.connections_online} of ${status.connections_total} connections online`;
  });

  protected logout() {
    this.api.logout().subscribe(() => this.router.navigateByUrl('/login'));
  }
}

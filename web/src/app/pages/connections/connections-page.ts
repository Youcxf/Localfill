import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ApiService } from '../../api/api.service';
import { EventsService } from '../../api/events.service';
import { AccountInfo, ConnectionHealth } from '../../api/types';
import { errorText } from '../../core/error-text';
import { AddConnectionDialog } from '../../shared/add-connection-dialog';
import { ConnectionCard, summarize, toCard } from './connection-cards';

@Component({
  selector: 'app-connections-page',
  imports: [AddConnectionDialog],
  templateUrl: './connections-page.html',
  styleUrl: './connections-page.css',
})
export class ConnectionsPage {
  private api = inject(ApiService);
  private events = inject(EventsService);

  protected readonly connections = signal<ConnectionHealth[]>([]);
  protected readonly loaded = signal(false);
  protected readonly showAdd = signal(false);
  // the label of the connection we are talking to tradovate for right now
  protected readonly busy = signal('');
  // a problem to show under a connection, by label
  protected readonly problems = signal<Record<string, string>>({});

  protected readonly cards = computed(() => this.connections().map(toCard));
  protected readonly summary = computed(() => summarize(this.connections()));

  constructor() {
    this.load();
    this.events.events$.pipe(takeUntilDestroyed()).subscribe((event) => {
      if (event.type === 'connections') {
        this.load();
      }
    });
  }

  // tick or untick an account. the api wants the full list of accounts that should get orders
  protected toggle(card: ConnectionCard, account: AccountInfo) {
    const ticked = card.accounts.filter((a) => a.is_target).map((a) => a.name);
    const next = account.is_target ? ticked.filter((name) => name !== account.name) : [...ticked, account.name];

    this.api.setTargets(card.label, next).subscribe({
      next: (updated) => this.replace(updated),
      error: (err) => this.problem(card.label, errorText(err)),
    });
  }

  protected reconnect(card: ConnectionCard) {
    this.busy.set(card.label);
    this.problem(card.label, '');
    this.api.reconnect(card.label).subscribe({
      next: (updated) => {
        this.busy.set('');
        this.replace(updated);
      },
      error: (err) => {
        this.busy.set('');
        this.problem(card.label, errorText(err));
        // the connection now has a fresh last_error, load it
        this.load();
      },
    });
  }

  protected remove(card: ConnectionCard) {
    if (!confirm(`Remove "${card.label}"? Its saved login is deleted, orders stop going to its accounts.`)) {
      return;
    }
    this.api.deleteConnection(card.label).subscribe({
      next: () => this.load(),
      error: (err) => this.problem(card.label, errorText(err)),
    });
  }

  protected added() {
    this.showAdd.set(false);
    this.load();
  }

  private load() {
    this.api.connections().subscribe((list) => {
      this.connections.set(list);
      this.loaded.set(true);
    });
  }

  private replace(updated: ConnectionHealth) {
    this.connections.update((list) => list.map((c) => (c.label === updated.label ? updated : c)));
  }

  private problem(label: string, text: string) {
    this.problems.update((all) => ({ ...all, [label]: text }));
  }
}

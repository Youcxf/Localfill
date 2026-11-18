import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ApiService } from '../../api/api.service';
import { EventsService } from '../../api/events.service';
import { AccountInfo, ConnectionHealth } from '../../api/types';
import { errorText } from '../../core/error-text';
import { AddConnectionDialog } from '../../shared/add-connection-dialog';
import {
  ConnectionCard,
  formatLimits,
  ordersText,
  parseLimits,
  summarize,
  toCard,
} from './connection-cards';

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
  protected readonly ordersText = ordersText;
  protected readonly formatLimits = formatLimits;

  // the account whose size limits are being edited ("label/id"), and what is typed in the field
  protected readonly editing = signal('');
  protected readonly limitText = signal('');

  constructor() {
    this.load();
    this.events.events$.pipe(takeUntilDestroyed()).subscribe((event) => {
      // an account showed up, closed or got locked by the firm
      if (event.type === 'connections' || event.type === 'accounts') {
        this.load();
      }
    });
  }

  // tick or untick an account. the api wants the full list of accounts that should get orders
  protected toggle(card: ConnectionCard, account: AccountInfo) {
    if (account.blocked) {
      return;
    }
    const ticked = card.accounts.filter((a) => a.is_target).map((a) => a.name);
    const next = account.is_target
      ? ticked.filter((name) => name !== account.name)
      : [...ticked, account.name];

    this.api.setTargets(card.label, next).subscribe({
      next: (updated) => this.replace(updated),
      error: (err) => this.problem(card.label, errorText(err)),
    });
  }

  // a blocked account is never traded or flattened, not even by flatten all.
  // only the dashboard can change this, an api token can not
  protected block(card: ConnectionCard, account: AccountInfo) {
    let next = [...card.blocked, account.name];
    if (account.blocked) {
      if (
        !confirm(
          `Unblock ${account.name}? Once ticked again it receives orders, and flatten all closes its positions.`,
        )
      ) {
        return;
      }
      next = card.blocked.filter((key) => key !== account.name && key !== String(account.id));
    }
    this.api.setBlocked(card.label, next).subscribe({
      next: (updated) => this.replace(updated),
      error: (err) => this.problem(card.label, errorText(err)),
    });
  }

  protected editLimits(card: ConnectionCard, account: AccountInfo) {
    this.editing.set(card.label + '/' + account.id);
    const text = Object.entries(account.max_contracts).map(
      ([product, limit]) => `${product}=${limit}`,
    );
    this.limitText.set(text.join(', '));
  }

  protected isEditing(card: ConnectionCard, account: AccountInfo) {
    return this.editing() === card.label + '/' + account.id;
  }

  protected saveLimits(card: ConnectionCard, account: AccountInfo) {
    const limits = parseLimits(this.limitText());
    if (limits === null) {
      this.problem(card.label, 'Limits look like NQ=1, MNQ=10');
      return;
    }
    this.api.setLimits(card.label, account.name, limits).subscribe({
      next: (updated) => {
        this.editing.set('');
        this.problem(card.label, '');
        this.replace(updated);
      },
      error: (err) => this.problem(card.label, errorText(err)),
    });
  }

  // loads the account list again, so an account you just bought shows up
  protected refreshAccounts(card: ConnectionCard) {
    this.busy.set(card.label);
    this.api.refreshAccounts(card.label).subscribe({
      next: (updated) => {
        this.busy.set('');
        this.replace(updated);
      },
      error: (err) => {
        this.busy.set('');
        this.problem(card.label, errorText(err));
      },
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

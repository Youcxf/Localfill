import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../api/api.service';
import { EventsService } from '../../api/events.service';
import { PnlResponse } from '../../api/types';
import { formatMoney } from '../../core/format';
import { toAccounts, toTrip, total } from './pnl-accounts';

@Component({
  selector: 'app-pnl-page',
  imports: [RouterLink],
  templateUrl: './pnl-page.html',
  styleUrl: './pnl-page.css',
})
export class PnlPage {
  private api = inject(ApiService);
  private events = inject(EventsService);

  protected readonly data = signal<PnlResponse | null>(null);
  // the account whose round trips are shown on the right
  protected readonly pickedKey = signal('');

  protected readonly accounts = computed(() => toAccounts(this.data() ?? { accounts: [] }));
  protected readonly loaded = computed(() => this.data() !== null);

  protected readonly sum = computed(() => total(this.accounts()));
  protected readonly figure = computed(() => (this.sum() === null ? '—' : formatMoney(this.sum() ?? 0)));
  protected readonly figureColor = computed(() => {
    const value = this.sum();
    return value === null ? 'muted' : value < 0 ? 'red' : 'green';
  });

  // "2 of 3 accounts in profit · 12 round trips · trade date 2026-09-20"
  protected readonly summary = computed(() => {
    const accounts = this.accounts();
    const inProfit = accounts.filter((a) => (a.pnl ?? 0) > 0).length;
    const trips = accounts.reduce((n, a) => n + a.pairs.length, 0);
    const date = accounts.find((a) => a.tradeDate)?.tradeDate;
    return `${inProfit} of ${accounts.length} accounts in profit · ${trips} round trips` + (date ? ` · trade date ${date}` : '');
  });

  protected readonly selected = computed(() => this.accounts().find((a) => a.key === this.pickedKey()) ?? this.accounts()[0]);
  protected readonly trips = computed(() => (this.selected()?.pairs ?? []).map(toTrip));

  constructor() {
    this.load();
    this.events.events$.pipe(takeUntilDestroyed()).subscribe((event) => {
      if (event.type === 'signal' && event.data.status !== 'pending') {
        this.load();
      }
    });
  }

  protected money(value: number | null): string {
    return value === null ? '—' : formatMoney(value);
  }

  private load() {
    this.api.pnl().subscribe((data) => this.data.set(data));
  }
}

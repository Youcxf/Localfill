import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ApiService } from '../../api/api.service';
import { EventsService } from '../../api/events.service';
import { PositionsResponse, SignalRecord } from '../../api/types';
import { formatSigned } from '../../core/format';
import { errorText } from '../../core/error-text';
import { FlattenDialog, FlattenLine } from '../../shared/flatten-dialog';
import { PositionGroup, groupPositions, rolloverNotice } from './position-groups';

// what the confirm dialog is about to do
interface Question {
  // one contract, or nothing for "everything"
  symbol?: string;
  title: string;
  text: string;
  confirmLabel: string;
  lines: FlattenLine[];
}

@Component({
  selector: 'app-positions-page',
  imports: [FlattenDialog],
  templateUrl: './positions-page.html',
  styleUrl: './positions-page.css',
})
export class PositionsPage {
  private api = inject(ApiService);
  private events = inject(EventsService);

  protected readonly data = signal<PositionsResponse | null>(null);
  protected readonly question = signal<Question | null>(null);
  protected readonly busy = signal(false);
  protected readonly lastFlatten = signal<SignalRecord | null>(null);
  protected readonly error = signal('');

  protected readonly groups = computed(() => groupPositions(this.data()?.positions ?? []));
  protected readonly notice = computed(() => rolloverNotice(this.groups()));

  // "5 open positions on 3 accounts, 3 contracts"
  protected readonly summary = computed(() => {
    const positions = this.data()?.positions ?? [];
    if (!positions.length) {
      return 'No open positions on any account.';
    }
    const accounts = new Set(positions.map((p) => `${p.connection}/${p.account_id}`)).size;
    const contracts = this.groups().length;
    return `${positions.length} open position${positions.length === 1 ? '' : 's'} on ${accounts} account${accounts === 1 ? '' : 's'}, ${contracts} contract${contracts === 1 ? '' : 's'}`;
  });

  constructor() {
    this.load();

    // positions change when a signal has been carried out
    this.events.events$.pipe(takeUntilDestroyed()).subscribe((event) => {
      if (event.type === 'signal' && event.data.status !== 'pending') {
        this.load();
      }
    });
  }

  protected askFlattenContract(group: PositionGroup) {
    this.question.set({
      symbol: group.contract,
      title: `Flatten ${group.contract}?`,
      text: `This closes ${group.rows.length} open position${group.rows.length === 1 ? '' : 's'} on ${group.accounts} account${group.accounts === 1 ? '' : 's'}, at market. It cannot be undone.`,
      confirmLabel: `Flatten ${group.contract}`,
      lines: [{ name: group.contract, net: 'net ' + formatSigned(group.net) }],
    });
  }

  protected askFlattenAll() {
    const positions = this.data()?.positions ?? [];
    const accounts = new Set(positions.map((p) => `${p.connection}/${p.account_id}`)).size;
    const connections = new Set(positions.map((p) => p.connection)).size;
    this.question.set({
      title: 'Flatten all positions?',
      text: `This closes ${positions.length} open position${positions.length === 1 ? '' : 's'} on ${accounts} account${accounts === 1 ? '' : 's'} across ${connections} connection${connections === 1 ? '' : 's'}, at market. It cannot be undone.`,
      confirmLabel: 'Flatten everything',
      lines: this.groups().map((g) => ({ name: g.contract, net: 'net ' + formatSigned(g.net) })),
    });
  }

  protected confirm() {
    const question = this.question();
    if (!question) {
      return;
    }
    this.busy.set(true);
    this.error.set('');
    this.api.flatten(question.symbol).subscribe({
      next: (record) => {
        this.busy.set(false);
        this.question.set(null);
        this.lastFlatten.set(record);
        this.load();
      },
      error: (err) => {
        this.busy.set(false);
        this.question.set(null);
        this.error.set(errorText(err));
      },
    });
  }

  private load() {
    this.api.positions().subscribe((data) => this.data.set(data));
  }
}

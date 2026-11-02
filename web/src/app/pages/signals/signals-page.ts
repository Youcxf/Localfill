import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ApiService } from '../../api/api.service';
import { EventsService } from '../../api/events.service';
import { SignalCounts, SignalRecord, SignalStatus } from '../../api/types';
import { StatusBadge } from '../../shared/status-badge';
import { toDetail, toRow } from './signal-rows';

type Filter = 'all' | SignalStatus;

// how many signals we ask for at once
const PAGE_SIZE = 25;

@Component({
  selector: 'app-signals-page',
  imports: [StatusBadge],
  templateUrl: './signals-page.html',
  styleUrl: './signals-page.css',
})
export class SignalsPage {
  private api = inject(ApiService);
  private events = inject(EventsService);

  protected readonly tabs: { key: Filter; label: string }[] = [
    { key: 'all', label: 'All' },
    { key: 'done', label: 'done' },
    { key: 'partial', label: 'partial' },
    { key: 'failed', label: 'failed' },
    { key: 'error', label: 'error' },
    { key: 'dry_run', label: 'dry run' },
    { key: 'duplicate', label: 'duplicate' },
    { key: 'pending', label: 'pending' },
  ];

  protected readonly filter = signal<Filter>('all');
  protected readonly records = signal<SignalRecord[]>([]);
  protected readonly counts = signal<SignalCounts | null>(null);
  protected readonly selectedId = signal<number | null>(null);
  protected readonly showRaw = signal(false);
  protected readonly loading = signal(true);

  protected readonly rows = computed(() => this.records().map(toRow));

  protected readonly detail = computed(() => {
    const record = this.records().find((r) => r.id === this.selectedId());
    return record ? toDetail(record) : null;
  });

  // how many signals the open tab has in total, so we know if there is more to load
  protected readonly total = computed(() => this.countOf(this.filter()));
  protected readonly hasMore = computed(() => this.records().length < this.total());

  constructor() {
    this.load();

    // a new signal, or a change to one we show (pending -> done)
    this.events.events$.pipe(takeUntilDestroyed()).subscribe((event) => {
      if (event.type === 'signal') {
        this.onSignal(event.data);
      }
    });
  }

  // the number on a tab
  protected countOf(key: Filter): number {
    const counts = this.counts();
    if (!counts) {
      return 0;
    }
    return key === 'all' ? counts.total : counts[key];
  }

  protected pick(key: Filter) {
    this.filter.set(key);
    this.load();
  }

  protected select(id: number) {
    this.selectedId.set(id);
    this.showRaw.set(false);
  }

  protected loadOlder() {
    this.api.signals(PAGE_SIZE, this.records().length, this.statusParam()).subscribe((older) => {
      this.records.update((list) => [...list, ...older]);
    });
  }

  private statusParam(): SignalStatus | undefined {
    const filter = this.filter();
    return filter === 'all' ? undefined : filter;
  }

  private load() {
    this.api.signals(PAGE_SIZE, 0, this.statusParam()).subscribe((list) => {
      this.records.set(list);
      this.loading.set(false);
      // keep the open signal if it is still in the list, otherwise open the newest one
      if (!list.some((r) => r.id === this.selectedId())) {
        this.selectedId.set(list[0]?.id ?? null);
      }
    });
    this.loadCounts();
  }

  private loadCounts() {
    this.api.signalCounts().subscribe((counts) => this.counts.set(counts));
  }

  private onSignal(record: SignalRecord) {
    const filter = this.filter();
    const matches = filter === 'all' || record.status === filter;

    this.records.update((list) => {
      const others = list.filter((r) => r.id !== record.id);
      const known = others.length !== list.length;
      if (!matches) {
        return others;
      }
      if (known) {
        return list.map((r) => (r.id === record.id ? record : r));
      }
      // a new signal goes on top
      return [record, ...list];
    });
    this.loadCounts();
  }
}

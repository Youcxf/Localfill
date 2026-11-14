import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../api/api.service';
import { ConnectionHealth, Preset, Signal, SignalRecord } from '../../api/types';
import { errorText } from '../../core/error-text';
import { StatusBadge } from '../../shared/status-badge';
import { summary, totalQty } from '../brackets/preset-form';
import { describe, toDetail } from '../signals/signal-rows';

type Action = 'buy' | 'sell' | 'flatten';
type OrderType = 'market' | 'limit' | 'stop';

@Component({
  selector: 'app-order-page',
  imports: [FormsModule, StatusBadge],
  templateUrl: './order-page.html',
  styleUrl: './order-page.css',
})
export class OrderPage {
  private api = inject(ApiService);

  protected readonly action = signal<Action>('buy');
  protected readonly symbol = signal('');
  protected readonly qty = signal(1);
  protected readonly orderType = signal<OrderType>('market');
  // text, so the field can be empty
  protected readonly price = signal('');
  // empty means every connection
  protected readonly connection = signal('');
  // a bracket preset by name, empty for a plain order
  protected readonly preset = signal('');
  // stop in points, empty for none (or the preset's backup stop)
  protected readonly stopLoss = signal('');

  protected readonly connections = signal<ConnectionHealth[]>([]);
  protected readonly presets = signal<Preset[]>([]);
  protected readonly result = signal<SignalRecord | null>(null);
  protected readonly sending = signal(false);
  protected readonly error = signal('');

  protected readonly isFlatten = computed(() => this.action() === 'flatten');
  protected readonly needsPrice = computed(() => !this.isFlatten() && this.orderType() !== 'market');

  protected readonly chosenPreset = computed(() => this.presets().find((p) => p.name === this.preset()) ?? null);

  // a preset with take profits sets the size itself
  protected readonly presetQty = computed(() => {
    const preset = this.chosenPreset();
    return preset && preset.take_profits.length ? totalQty(preset) : null;
  });

  protected readonly presetSummary = computed(() => {
    const preset = this.chosenPreset();
    return preset ? summary(preset) : '';
  });

  // how many accounts and connections this order would go to
  protected readonly targets = computed(() => {
    const chosen = this.connection()
      ? this.connections().filter((c) => c.label === this.connection())
      : this.connections();
    const receiving = chosen.filter((c) => c.accounts.some((a) => a.is_target));
    return {
      accounts: chosen.reduce((n, c) => n + c.accounts.filter((a) => a.is_target).length, 0),
      connections: receiving.length,
      live: receiving.some((c) => !c.demo),
    };
  });

  // "BUY 2 MNQ" or "FLATTEN MNQ"
  protected readonly what = computed(() => {
    const symbol = this.symbol().trim().toUpperCase();
    if (this.isFlatten()) {
      return `FLATTEN ${symbol || 'everything'}`;
    }
    return `${this.action().toUpperCase()} ${this.presetQty() ?? (this.qty() || 0)} ${symbol || '(symbol)'}`;
  });

  // "at market" or "as a limit order at 19,980"
  protected readonly how = computed(() => {
    if (this.isFlatten()) {
      return 'at market';
    }
    let text = this.orderType() === 'market' ? 'at market' : `as a ${this.orderType()} order at ${this.price() || '(price)'}`;
    if (this.preset()) {
      text += ` with the ${this.preset()} bracket`;
    }
    if (this.stopLoss().trim()) {
      text += `${this.preset() ? ' and' : ' with'} a ${this.stopLoss().trim()} point stop`;
    }
    return text;
  });

  // the finished signal in the words the history uses
  protected readonly detail = computed(() => {
    const record = this.result();
    return record ? toDetail(record) : null;
  });

  protected readonly sent = computed(() => {
    const record = this.result();
    if (!record) {
      return '';
    }
    const payload = record.payload;
    const type = payload.action === 'flatten' ? '' : `, ${payload.order_type ?? 'market'}`;
    return `${(payload.action ?? '').toUpperCase()} ${describe(record)}${type}`;
  });

  constructor() {
    this.api.connections().subscribe((list) => this.connections.set(list));
    this.api.presets().subscribe((list) => this.presets.set(list));
  }

  protected reset() {
    this.action.set('buy');
    this.symbol.set('');
    this.qty.set(1);
    this.orderType.set('market');
    this.price.set('');
    this.connection.set('');
    this.preset.set('');
    this.stopLoss.set('');
    this.error.set('');
  }

  protected send() {
    const problem = this.check();
    if (problem) {
      this.error.set(problem);
      return;
    }

    const signal: Signal = { action: this.action() };
    const symbol = this.symbol().trim();
    if (symbol) {
      signal.symbol = symbol;
    }
    if (!this.isFlatten()) {
      signal.qty = this.qty();
      signal.order_type = this.orderType();
      if (this.needsPrice()) {
        signal.price = Number(this.price());
      }
      if (this.preset()) {
        signal.preset = this.preset();
      }
      if (this.stopLoss().trim()) {
        signal.stop_loss = Number(this.stopLoss());
      }
    }
    if (this.connection()) {
      signal.connection = this.connection();
    }

    this.error.set('');
    this.sending.set(true);
    // this call waits until tradovate has answered for every account
    this.api.sendSignal(signal).subscribe({
      next: (record) => {
        this.sending.set(false);
        this.result.set(record);
      },
      error: (err) => {
        this.sending.set(false);
        this.error.set(errorText(err));
      },
    });
  }

  // what is wrong with the form, empty when it is fine
  private check(): string {
    if (!this.isFlatten() && !this.symbol().trim()) {
      return 'Enter a symbol, like MNQ.';
    }
    if (!this.isFlatten() && !(Number.isInteger(this.qty()) && this.qty() >= 1)) {
      return 'The quantity must be a whole number, 1 or more.';
    }
    if (this.needsPrice() && !(Number(this.price()) > 0)) {
      return 'A limit or stop order needs a price.';
    }
    if (!this.isFlatten() && this.stopLoss().trim() && !(Number(this.stopLoss()) > 0)) {
      return 'The stop is in points and must be more than 0.';
    }
    if (!this.isFlatten() && this.chosenPreset()?.stop_loss == null && this.preset() && !this.stopLoss().trim()) {
      return `The ${this.preset()} preset has no backup stop, enter one.`;
    }
    return '';
  }
}

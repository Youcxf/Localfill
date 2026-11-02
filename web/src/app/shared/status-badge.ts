import { Component, computed, input } from '@angular/core';
import { SignalStatus } from '../api/types';

// the small tag that says what state a signal is in. it is words only on purpose,
// the color helps but the word always says it
@Component({
  selector: 'app-status-badge',
  template: `<span class="badge" [attr.data-status]="status()">{{ label() }}</span>`,
  styleUrl: './status-badge.css',
})
export class StatusBadge {
  readonly status = input.required<SignalStatus>();

  protected readonly label = computed(() => (this.status() === 'dry_run' ? 'dry run' : this.status()));
}

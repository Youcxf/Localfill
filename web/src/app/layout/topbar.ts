import { Component, computed, inject, input, signal } from '@angular/core';
import { ApiService } from '../api/api.service';
import { EventsService } from '../api/events.service';
import { AppState } from '../core/app-state';

@Component({
  selector: 'app-topbar',
  templateUrl: './topbar.html',
  styleUrl: './topbar.css',
})
export class Topbar {
  private api = inject(ApiService);
  private state = inject(AppState);
  private events = inject(EventsService);

  readonly title = input.required<string>();

  protected readonly dry = computed(() => this.state.status()?.dry_run ?? false);
  protected readonly halted = computed(() => this.state.status()?.halted ?? false);
  protected readonly connected = this.events.connected;
  // true while the halt is flattening the accounts
  protected readonly halting = signal(false);

  protected turnOffDryRun() {
    this.api.setDryRun(false).subscribe(() => this.state.refresh());
  }

  // the kill switch: flattens every target account now, without waiting for the signals in the queue
  protected halt() {
    if (
      !confirm(
        'Halt? Every target account is flattened now, and new buys and sells are refused until you resume.',
      )
    ) {
      return;
    }
    this.halting.set(true);
    this.api.halt().subscribe({
      next: () => {
        this.halting.set(false);
        this.state.refresh();
      },
      error: () => {
        this.halting.set(false);
        this.state.refresh();
      },
    });
  }

  protected resume() {
    if (!confirm('Resume? Buys and sells are accepted again.')) {
      return;
    }
    this.api.resume().subscribe(() => this.state.refresh());
  }
}

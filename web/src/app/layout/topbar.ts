import { Component, computed, inject, input } from '@angular/core';
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
  protected readonly connected = this.events.connected;

  protected turnOffDryRun() {
    this.api.setDryRun(false).subscribe(() => this.state.refresh());
  }
}

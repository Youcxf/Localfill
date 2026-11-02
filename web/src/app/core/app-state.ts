import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from '../api/api.service';
import { Status } from '../api/types';

// the small summary that the sidebar and the top bar both show (dry run, connections online, version)
@Injectable({ providedIn: 'root' })
export class AppState {
  private api = inject(ApiService);

  readonly status = signal<Status | null>(null);

  // asks the server again, called on start and when a settings or connections event arrives
  refresh() {
    this.api.status().subscribe({
      next: (status) => this.status.set(status),
      // a 401 is handled by the interceptor, anything else just keeps the old value
      error: () => undefined,
    });
  }
}

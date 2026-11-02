import { Component, inject, signal } from '@angular/core';
import { ApiService } from '../api/api.service';
import { Logo } from './logo';

// the page around the login and setup forms: logo top left, the form on the left, a footer line
@Component({
  selector: 'app-auth-frame',
  imports: [Logo],
  template: `
    <div class="page">
      <app-logo [width]="184" />

      <main>
        <ng-content />
      </main>

      <footer>
        <span>This dashboard only listens on this computer.</span>
        <span class="mono">v{{ version() }}</span>
      </footer>
    </div>
  `,
  styles: `
    .page {
      display: flex;
      flex-direction: column;
      min-height: 100vh;
      padding: 40px;
    }

    main {
      display: flex;
      flex: 1;
      flex-direction: column;
      justify-content: center;
      padding: 32px 0 32px 200px;
    }

    footer {
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      color: var(--muted);
    }

    @media (max-width: 900px) {
      main {
        padding-left: 0;
      }
    }
  `,
})
export class AuthFrame {
  protected readonly version = signal('');

  constructor() {
    inject(ApiService)
      .health()
      .subscribe({ next: (health) => this.version.set(health.version), error: () => undefined });
  }
}

import { Component, computed, inject, signal } from '@angular/core';
import { ApiService } from '../../api/api.service';
import { Settings } from '../../api/types';
import { AppState } from '../../core/app-state';
import { errorText } from '../../core/error-text';

// alert messages people can paste into TradingView. the {{ }} parts are tradingview placeholders,
// tradingview fills them in when the alert fires
const SNIPPETS = [
  {
    title: 'Strategy alert, buys and sells',
    code: '{"action": "{{strategy.order.action}}", "symbol": "{{ticker}}", "qty": {{strategy.order.contracts}}}',
  },
  {
    title: 'Exit alert, close everything on the symbol',
    code: '{"action": "flatten", "symbol": "{{ticker}}"}',
  },
];

@Component({
  selector: 'app-settings-page',
  templateUrl: './settings-page.html',
  styleUrl: './settings-page.css',
})
export class SettingsPage {
  private api = inject(ApiService);
  private state = inject(AppState);

  protected readonly snippets = SNIPPETS;

  protected readonly settings = signal<Settings | null>(null);
  protected readonly reveal = signal(false);
  // which copy button just worked, so it can say "Copied" for a moment
  protected readonly copied = signal('');
  protected readonly error = signal('');

  // the address without the secret part, like "https://alerts.example.com/hook/"
  protected readonly base = computed(() => {
    const settings = this.settings();
    if (!settings) {
      return '';
    }
    if (settings.webhook_url) {
      return settings.webhook_url.slice(0, settings.webhook_url.lastIndexOf('/') + 1);
    }
    return 'https://<your tunnel address>/hook/';
  });

  // the token is hidden until the user asks for it, it works like a password
  protected readonly shown = computed(() => {
    const settings = this.settings();
    return this.base() + (this.reveal() ? (settings?.webhook_token ?? '') : '•'.repeat(24));
  });

  constructor() {
    this.api.settings().subscribe((settings) => this.settings.set(settings));
  }

  protected setDryRun(on: boolean) {
    this.api.setDryRun(on).subscribe({
      next: (settings) => {
        this.settings.set(settings);
        // the top bar shows dry run too
        this.state.refresh();
      },
      error: (err) => this.error.set(errorText(err)),
    });
  }

  protected regenerate() {
    if (!confirm('Make a new webhook URL? The old one stops working right away, so every TradingView alert that uses it has to be updated.')) {
      return;
    }
    this.api.regenerateWebhookToken().subscribe({
      next: (settings) => {
        this.settings.set(settings);
        this.reveal.set(false);
      },
      error: (err) => this.error.set(errorText(err)),
    });
  }

  // copies the real address, not the dots. key says which button to mark as copied
  protected async copyUrl() {
    const settings = this.settings();
    if (settings) {
      await this.copy(settings.webhook_url ?? settings.webhook_path, 'url');
    }
  }

  protected async copy(text: string, key: string) {
    try {
      await navigator.clipboard.writeText(text);
      this.copied.set(key);
      setTimeout(() => this.copied.set(''), 1500);
    } catch {
      // browsers only allow this on localhost or https
      this.error.set('Could not copy, select the text and copy it by hand.');
    }
  }
}

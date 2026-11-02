import { AfterViewInit, Component, ElementRef, inject, output, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../api/api.service';
import { ConnectionConfig } from '../api/types';
import { errorText } from '../core/error-text';

// the form to add a tradovate login. localfill logs in first, so a wrong password shows up right here
@Component({
  selector: 'app-add-connection-dialog',
  imports: [FormsModule],
  templateUrl: './add-connection-dialog.html',
  styleUrl: './add-connection-dialog.css',
})
export class AddConnectionDialog implements AfterViewInit {
  private api = inject(ApiService);

  readonly saved = output<void>();
  readonly cancelled = output<void>();

  private readonly box = viewChild.required<ElementRef<HTMLDialogElement>>('box');

  protected readonly label = signal('');
  protected readonly username = signal('');
  protected readonly password = signal('');
  protected readonly live = signal(false);
  protected readonly apiKey = signal(false);
  protected readonly cid = signal('');
  protected readonly apiSecret = signal('');

  protected readonly error = signal('');
  protected readonly busy = signal(false);

  ngAfterViewInit() {
    this.box().nativeElement.showModal();
  }

  protected submit() {
    if (!this.label().trim() || !this.username() || !this.password()) {
      this.error.set('label, username and password are all needed');
      return;
    }

    const config: ConnectionConfig = {
      label: this.label().trim(),
      username: this.username(),
      password: this.password(),
      demo: !this.live(),
      auth_mode: this.apiKey() ? 'api_key' : 'web',
    };
    if (this.apiKey()) {
      config.cid = this.cid();
      config.api_secret = this.apiSecret();
    }

    this.error.set('');
    this.busy.set(true);
    this.api.addConnection(config).subscribe({
      next: () => this.saved.emit(),
      error: (err) => {
        this.busy.set(false);
        this.error.set(errorText(err));
      },
    });
  }
}

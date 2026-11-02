import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ApiService } from '../../api/api.service';
import { errorText } from '../../core/error-text';
import { AuthFrame } from '../../shared/auth-frame';

@Component({
  selector: 'app-login-page',
  imports: [FormsModule, AuthFrame],
  templateUrl: './login-page.html',
  styleUrl: './login-page.css',
})
export class LoginPage {
  private api = inject(ApiService);
  private router = inject(Router);

  protected readonly password = signal('');
  protected readonly error = signal('');
  protected readonly busy = signal(false);

  constructor() {
    // send people to the right place: setup if no password exists yet, the dashboard if already in
    this.api.authStatus().subscribe((status) => {
      if (status.setup_required) {
        this.router.navigateByUrl('/setup');
      } else if (status.logged_in) {
        this.router.navigateByUrl('/');
      }
    });
  }

  protected submit() {
    if (!this.password()) {
      return;
    }
    this.error.set('');
    this.busy.set(true);
    this.api.login(this.password()).subscribe({
      next: () => this.router.navigateByUrl('/'),
      error: (err) => {
        this.busy.set(false);
        this.error.set(errorText(err));
      },
    });
  }
}

import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ApiService } from '../../api/api.service';
import { errorText } from '../../core/error-text';
import { AuthFrame } from '../../shared/auth-frame';

// first run only: choose the dashboard password
@Component({
  selector: 'app-setup-page',
  imports: [FormsModule, AuthFrame],
  templateUrl: './setup-page.html',
  styleUrl: './setup-page.css',
})
export class SetupPage {
  private api = inject(ApiService);
  private router = inject(Router);

  protected readonly password = signal('');
  protected readonly repeat = signal('');
  protected readonly serverError = signal('');
  protected readonly busy = signal(false);
  // we only show what is wrong with the form after the first try
  protected readonly tried = signal(false);

  // what is wrong with the form right now, empty when it is fine
  protected readonly problem = computed(() => {
    if (this.password().length < 8) {
      return 'The password needs at least 8 characters.';
    }
    if (this.password() !== this.repeat()) {
      return 'The two passwords are not the same.';
    }
    return '';
  });

  constructor() {
    // if a password already exists, this page is not for you
    this.api.authStatus().subscribe((status) => {
      if (!status.setup_required) {
        this.router.navigateByUrl(status.logged_in ? '/' : '/login');
      }
    });
  }

  protected submit() {
    this.tried.set(true);
    if (this.problem()) {
      return;
    }
    this.serverError.set('');
    this.busy.set(true);
    this.api.setup(this.password()).subscribe({
      next: () => this.router.navigateByUrl('/'),
      error: (err) => {
        this.busy.set(false);
        this.serverError.set(errorText(err));
      },
    });
  }
}

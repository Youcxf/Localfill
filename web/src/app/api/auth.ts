import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, map, throwError } from 'rxjs';
import { ApiService } from './api.service';

// put this on every route except /login and /setup:
//   { path: 'connections', component: ConnectionsPage, canActivate: [authGuard] }
export const authGuard: CanActivateFn = () => {
  const api = inject(ApiService);
  const router = inject(Router);

  return api.authStatus().pipe(
    map((status) => {
      if (status.setup_required) return router.parseUrl('/setup');
      if (!status.logged_in) return router.parseUrl('/login');
      return true;
    }),
  );
};

// when any call answers 401 (the session ended, or the server restarted), go to the login page.
// register it in app.config.ts:  provideHttpClient(withInterceptors([authInterceptor]))
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const router = inject(Router);

  return next(request).pipe(
    catchError((error: HttpErrorResponse) => {
      // a wrong password on the login form is also a 401, that one stays on the form
      const isLoginForm = request.url.endsWith('/auth/login');
      if (error.status === 401 && !isLoginForm) {
        router.navigateByUrl('/login');
      }
      return throwError(() => error);
    }),
  );
};

import { Component, inject } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter, map, startWith } from 'rxjs';
import { EventsService } from '../api/events.service';
import { AppState } from '../core/app-state';
import { Sidebar } from './sidebar';
import { Topbar } from './topbar';

// the frame around every screen after login: sidebar on the left, top bar and the page on the right
@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, Sidebar, Topbar],
  templateUrl: './shell.html',
  styleUrl: './shell.css',
})
export class Shell {
  private router = inject(Router);
  private state = inject(AppState);
  private events = inject(EventsService);

  // each route says its title in its data (see app.routes.ts)
  protected readonly title = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map(() => this.currentTitle()),
      startWith(this.currentTitle()),
    ),
    { initialValue: '' },
  );

  constructor() {
    this.state.refresh();

    // the sidebar and top bar show settings and connection info, keep them fresh
    this.events.events$.pipe(takeUntilDestroyed()).subscribe((event) => {
      if (event.type === 'settings' || event.type === 'connections') {
        this.state.refresh();
      }
    });
  }

  // walks down to the page that is open now and reads its title
  private currentTitle(): string {
    let route = this.router.routerState.snapshot.root;
    while (route.firstChild) {
      route = route.firstChild;
    }
    return route.data['title'] ?? '';
  }
}

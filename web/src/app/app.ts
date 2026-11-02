import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

// the root only holds the router, every screen is a route (see app.routes.ts)
@Component({
  selector: 'app-root',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
export class App {}

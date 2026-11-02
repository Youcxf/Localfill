import { Injectable, signal } from '@angular/core';
import { Observable, retry, share } from 'rxjs';
import { SseEvent } from './types';

// live updates from the server. use it like this:
//
//   events.events$.subscribe((event) => {
//     if (event.type === 'signal') { /* add or replace event.data in a list, match on id */ }
//     if (event.type === 'connections') { /* load the connections again */ }
//   });
//
// the connection opens when the first component subscribes and closes when the last one leaves.
// only subscribe after the user is logged in (the stream needs the session cookie)
@Injectable({ providedIn: 'root' })
export class EventsService {
  // true while the browser has the stream open, the top bar shows it
  readonly connected = signal(false);

  readonly events$ = new Observable<SseEvent>((subscriber) => {
    const source = new EventSource('/api/events');

    source.onopen = () => this.connected.set(true);
    source.onmessage = (message) => subscriber.next(JSON.parse(message.data));

    // the browser reconnects by itself when the network drops. but when the server
    // answers with an error (like 401) it gives up, and then we start a new one below
    source.onerror = () => {
      this.connected.set(false);
      if (source.readyState === EventSource.CLOSED) {
        subscriber.error(new Error('event stream closed'));
      }
    };

    return () => {
      source.close();
      this.connected.set(false);
    };
  }).pipe(
    retry({ delay: 3000 }),
    share(),
  );
}

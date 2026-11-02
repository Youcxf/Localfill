import { AfterViewInit, Component, ElementRef, input, output, viewChild } from '@angular/core';

export interface FlattenLine {
  name: string;
  net: string;
}

// asks "are you sure?" before closing positions. the page that uses it shows it with @if
// and removes it again when it gets confirmed or cancelled
@Component({
  selector: 'app-flatten-dialog',
  templateUrl: './flatten-dialog.html',
  styleUrl: './flatten-dialog.css',
})
export class FlattenDialog implements AfterViewInit {
  readonly title = input.required<string>();
  readonly text = input.required<string>();
  readonly lines = input.required<FlattenLine[]>();
  readonly confirmLabel = input('Flatten everything');
  readonly busy = input(false);

  readonly confirmed = output<void>();
  readonly cancelled = output<void>();

  private readonly box = viewChild.required<ElementRef<HTMLDialogElement>>('box');

  ngAfterViewInit() {
    this.box().nativeElement.showModal();
  }
}

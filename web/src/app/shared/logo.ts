import { Component, computed, input } from '@angular/core';

// the logo file (logo.png in the repository root, copied over by npm start and npm run build) has
// empty space around the logo, so we size the box to the visible part and move the picture inside it.
// the file is 1056 x 483 px and the visible logo (icon + name) is 783 x 135 px, starting at x 120, y 178.
// if you export the logo with another size or position, change these numbers
const IMAGE_WIDTH = 1056;
const IMAGE_HEIGHT = 483;
const VISIBLE_WIDTH = 783;
const VISIBLE_HEIGHT = 135;
const VISIBLE_X = 120;
const VISIBLE_Y = 178;

@Component({
  selector: 'app-logo',
  template: `
    <span class="box" [style.width.px]="width()" [style.height.px]="height()">
      <img
        src="logo.png"
        alt="Localfill"
        [style.left.px]="-VISIBLE_X * scale()"
        [style.top.px]="-VISIBLE_Y * scale()"
        [style.width.px]="IMAGE_WIDTH * scale()"
        [style.height.px]="IMAGE_HEIGHT * scale()"
      />
    </span>
  `,
  styles: `
    .box {
      position: relative;
      display: block;
    }

    img {
      position: absolute;
      max-width: none;
      pointer-events: none;
    }
  `,
})
export class Logo {
  // how wide the visible logo should be, in px
  readonly width = input(150);

  protected readonly IMAGE_WIDTH = IMAGE_WIDTH;
  protected readonly IMAGE_HEIGHT = IMAGE_HEIGHT;
  protected readonly VISIBLE_X = VISIBLE_X;
  protected readonly VISIBLE_Y = VISIBLE_Y;

  protected readonly scale = computed(() => this.width() / VISIBLE_WIDTH);
  protected readonly height = computed(() => Math.round(VISIBLE_HEIGHT * this.scale()));
}

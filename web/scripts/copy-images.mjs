// copies logo.png and icon.png from the repository root into public/, so the app always uses the latest ones.
// the copies are not committed (see .gitignore), edit the ones in the root
import { copyFileSync, mkdirSync } from 'node:fs';

mkdirSync(new URL('../public/', import.meta.url), { recursive: true });

for (const name of ['logo.png', 'icon.png']) {
  copyFileSync(new URL('../../' + name, import.meta.url), new URL('../public/' + name, import.meta.url));
  console.log(name + ' copied from the repository root');
}

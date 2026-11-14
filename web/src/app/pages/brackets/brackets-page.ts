import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../api/api.service';
import { EventsService } from '../../api/events.service';
import { IfOpen, Preset } from '../../api/types';
import { errorText } from '../../core/error-text';
import { IF_OPEN_TEXT, PartRow, PresetForm, alertFor, emptyForm, summary, toConfig, toForm, totalQty } from './preset-form';

@Component({
  selector: 'app-brackets-page',
  imports: [FormsModule],
  templateUrl: './brackets-page.html',
  styleUrl: './brackets-page.css',
})
export class BracketsPage {
  private api = inject(ApiService);
  private events = inject(EventsService);

  protected readonly ifOpenText = IF_OPEN_TEXT;
  // a tradingview placeholder, kept out of the template so angular does not read it
  protected readonly closeTag = '{{close}}';

  protected readonly presets = signal<Preset[]>([]);
  protected readonly loaded = signal(false);
  // the name of the preset in the form, empty while making a new one
  protected readonly editing = signal('');
  protected readonly form = signal<PresetForm>(emptyForm());
  protected readonly saving = signal(false);
  protected readonly error = signal('');
  protected readonly saved = signal('');
  protected readonly copied = signal(false);

  protected readonly rows = computed(() => this.presets().map((p) => ({ name: p.name, summary: summary(p) })));

  // the form as a preset, to show what it does before saving
  protected readonly preview = computed(() => {
    const out = toConfig({ ...this.form(), name: this.form().name || 'preview' });
    return 'config' in out ? out.config : null;
  });

  protected readonly total = computed(() => {
    const config = this.preview();
    return config ? totalQty(config) : 0;
  });

  protected readonly alert = computed(() => {
    const config = this.preview();
    return config ? alertFor(this.form().name.trim() || 'name', config) : '';
  });

  // the take profits breakeven can wait for: the ones that have points
  protected readonly triggers = computed(() =>
    this.form()
      .parts.map((part, i) => ({ number: i + 1, points: part.points.trim() }))
      .filter((p) => p.points !== ''),
  );

  constructor() {
    this.load();
    this.events.events$.pipe(takeUntilDestroyed()).subscribe((event) => {
      if (event.type === 'presets') {
        this.load();
      }
    });
  }

  protected edit(name: string) {
    const preset = this.presets().find((p) => p.name === name);
    if (preset) {
      this.editing.set(name);
      this.form.set(toForm(preset));
      this.clearMessages();
    }
  }

  protected startNew() {
    this.editing.set('');
    this.form.set(emptyForm());
    this.clearMessages();
  }

  protected set<K extends keyof PresetForm>(key: K, value: PresetForm[K]) {
    this.form.update((form) => ({ ...form, [key]: value }));
  }

  protected setPart(index: number, key: keyof PartRow, value: string) {
    const parts = this.form().parts.map((part, i) => (i === index ? { ...part, [key]: value } : part));
    this.set('parts', parts);
  }

  protected addPart() {
    this.set('parts', [...this.form().parts, { qty: '1', points: '' }]);
  }

  protected removePart(index: number) {
    const parts = this.form().parts.filter((_, i) => i !== index);
    // breakeven can not wait for a take profit that is gone
    const after = Number(this.form().breakevenAfter);
    this.form.update((form) => ({ ...form, parts, breakevenAfter: after > parts.length ? '' : form.breakevenAfter }));
  }

  protected setIfOpen(value: IfOpen) {
    this.set('ifOpen', value);
  }

  protected save() {
    const out = toConfig(this.form());
    if ('problem' in out) {
      this.error.set(out.problem);
      return;
    }
    const name = this.form().name.trim();
    this.clearMessages();
    this.saving.set(true);
    this.api.savePreset(name, out.config).subscribe({
      next: (preset) => {
        this.saving.set(false);
        this.editing.set(preset.name);
        this.saved.set(`Saved ${preset.name}.`);
        this.load();
      },
      error: (err) => {
        this.saving.set(false);
        this.error.set(errorText(err));
      },
    });
  }

  protected remove() {
    const name = this.editing();
    if (!name || !confirm(`Delete the preset ${name}? Alerts that use it will fail until you make it again.`)) {
      return;
    }
    this.api.deletePreset(name).subscribe({
      next: () => {
        this.startNew();
        this.load();
      },
      error: (err) => this.error.set(errorText(err)),
    });
  }

  protected async copyAlert() {
    try {
      await navigator.clipboard.writeText(this.alert());
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 1500);
    } catch {
      // browsers only allow this on localhost or https
      this.error.set('Could not copy, select the text and copy it by hand.');
    }
  }

  private load() {
    this.api.presets().subscribe({
      next: (list) => {
        this.presets.set(list);
        this.loaded.set(true);
      },
      error: (err) => this.error.set(errorText(err)),
    });
  }

  private clearMessages() {
    this.error.set('');
    this.saved.set('');
  }
}

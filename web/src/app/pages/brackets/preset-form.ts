import { IfOpen, Preset, PresetConfig } from '../../api/types';

// the bracket form works with text (so a field can be empty) and turns it into a preset here.
// it lives away from the component, so it is easy to read and to test

export interface PartRow {
  qty: string;
  points: string;
}

export interface PresetForm {
  name: string;
  parts: PartRow[];
  stopLoss: string;
  maxStop: string;
  // '' means no breakeven, otherwise the number of the take profit
  breakevenAfter: string;
  breakevenTicks: string;
  ifOpen: IfOpen;
}

// what each choice does, shown under the buttons
export const IF_OPEN_TEXT: Record<IfOpen, string> = {
  reverse: 'An opposite position is closed first, with its take profits and stop. A position on the same side gets this bracket added on top.',
  replace: 'Any position on that contract is closed first, so there is only ever one bracket.',
  skip: 'An account that already holds the contract gets nothing.',
};

// the requester's example, a good start for a new preset
export function emptyForm(): PresetForm {
  return {
    name: '',
    parts: [
      { qty: '5', points: '8' },
      { qty: '5', points: '20' },
    ],
    stopLoss: '',
    maxStop: '',
    breakevenAfter: '1',
    breakevenTicks: '2',
    ifOpen: 'reverse',
  };
}

export function toForm(preset: Preset): PresetForm {
  return {
    name: preset.name,
    parts: preset.take_profits.map((p) => ({ qty: String(p.qty), points: p.points == null ? '' : String(p.points) })),
    stopLoss: preset.stop_loss == null ? '' : String(preset.stop_loss),
    maxStop: preset.max_stop_loss == null ? '' : String(preset.max_stop_loss),
    breakevenAfter: preset.breakeven_after == null ? '' : String(preset.breakeven_after),
    breakevenTicks: String(preset.breakeven_ticks),
    ifOpen: preset.if_open,
  };
}

// the preset to save, or what is wrong with the form
export function toConfig(form: PresetForm): { config: PresetConfig } | { problem: string } {
  if (!/^[\w .-]{1,50}$/.test(form.name.trim())) {
    return { problem: 'Give the preset a name: letters, numbers, spaces, dots and dashes.' };
  }
  const takeProfits = [];
  for (const [i, part] of form.parts.entries()) {
    const qty = Number(part.qty);
    if (!(Number.isInteger(qty) && qty >= 1)) {
      return { problem: `Part ${i + 1} needs a whole number of contracts.` };
    }
    const points = part.points.trim() === '' ? null : Number(part.points);
    if (points !== null && !(points > 0)) {
      return { problem: `The take profit of part ${i + 1} must be more than 0 points, or empty.` };
    }
    takeProfits.push({ qty, points });
  }
  const stopLoss = optionalPoints(form.stopLoss);
  const maxStop = optionalPoints(form.maxStop);
  if (Number.isNaN(stopLoss) || Number.isNaN(maxStop)) {
    return { problem: 'Stops are in points and must be more than 0, or empty.' };
  }
  const ticks = Number(form.breakevenTicks || '0');
  if (!Number.isInteger(ticks)) {
    return { problem: 'The breakeven ticks must be a whole number.' };
  }
  return {
    config: {
      take_profits: takeProfits,
      stop_loss: stopLoss,
      max_stop_loss: maxStop,
      breakeven_after: form.breakevenAfter ? Number(form.breakevenAfter) : null,
      breakeven_ticks: ticks,
      if_open: form.ifOpen,
    },
  };
}

// empty is null, anything that is not more than 0 is NaN
function optionalPoints(text: string): number | null {
  if (text.trim() === '') {
    return null;
  }
  const value = Number(text);
  return value > 0 ? value : NaN;
}

// "5 at +8, 5 at +20 · stop 15, max 30 · breakeven after TP 1, +2 ticks"
export function summary(preset: PresetConfig): string {
  const parts = preset.take_profits.map((p) => (p.points == null ? `${p.qty} stop only` : `${p.qty} at +${p.points}`));
  const words = [parts.join(', ') || 'alert qty, stop only'];

  const stops = [];
  if (preset.stop_loss != null) {
    stops.push(`stop ${preset.stop_loss}`);
  }
  if (preset.max_stop_loss != null) {
    stops.push(`max ${preset.max_stop_loss}`);
  }
  words.push(stops.length ? stops.join(', ') : 'stop from the alert');

  if (preset.breakeven_after != null) {
    const ticks = preset.breakeven_ticks;
    words.push(`breakeven after TP ${preset.breakeven_after}, ${ticks >= 0 ? '+' : ''}${ticks} ticks`);
  }
  return words.join(' · ');
}

// contracts per account
export function totalQty(preset: PresetConfig): number {
  return preset.take_profits.reduce((n, p) => n + p.qty, 0);
}

// the tradingview alert message that uses this preset. without a backup stop the alert must send one
export function alertFor(name: string, preset: PresetConfig): string {
  const stop = preset.stop_loss == null ? ', "stop_loss": 10' : '';
  return `{"action": "{{strategy.order.action}}", "symbol": "{{ticker}}", "preset": "${name}"${stop}}`;
}

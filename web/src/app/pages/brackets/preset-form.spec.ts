import { Preset } from '../../api/types';
import { alertFor, emptyForm, summary, toConfig, toForm } from './preset-form';

function preset(changes: Partial<Preset> = {}): Preset {
  return {
    name: 'nq split',
    take_profits: [
      { qty: 5, points: 8 },
      { qty: 5, points: 20 },
    ],
    stop_loss: 15,
    max_stop_loss: null,
    breakeven_after: 1,
    breakeven_ticks: 2,
    if_open: 'reverse',
    ...changes,
  };
}

describe('preset form', () => {
  it('turns the form into a preset', () => {
    const form = { ...emptyForm(), name: 'nq split', stopLoss: '15' };

    expect(toConfig(form)).toEqual({
      config: {
        take_profits: [
          { qty: 5, points: 8 },
          { qty: 5, points: 20 },
        ],
        stop_loss: 15,
        max_stop_loss: null,
        breakeven_after: 1,
        breakeven_ticks: 2,
        if_open: 'reverse',
      },
    });
  });

  it('keeps an empty take profit as a part with only the stop', () => {
    const form = { ...emptyForm(), name: 'runner', parts: [{ qty: '2', points: '' }] };

    const out = toConfig(form);

    expect('config' in out && out.config.take_profits).toEqual([{ qty: 2, points: null }]);
  });

  it('says what is wrong', () => {
    expect(toConfig({ ...emptyForm(), name: '' })).toEqual({ problem: expect.stringContaining('name') });
    expect(toConfig({ ...emptyForm(), name: 'a', parts: [{ qty: '1.5', points: '8' }] })).toEqual({
      problem: 'Part 1 needs a whole number of contracts.',
    });
    expect(toConfig({ ...emptyForm(), name: 'a', stopLoss: '-3' })).toEqual({
      problem: 'Stops are in points and must be more than 0, or empty.',
    });
  });

  it('goes back and forth without changing anything', () => {
    const out = toConfig(toForm(preset()));

    expect('config' in out && { ...out.config, name: 'nq split' }).toEqual(preset());
  });

  it('sums up a preset in one line', () => {
    expect(summary(preset())).toBe('5 at +8, 5 at +20 · stop 15 · breakeven after TP 1, +2 ticks');
    expect(summary(preset({ stop_loss: null, max_stop_loss: 30, breakeven_after: null }))).toBe(
      '5 at +8, 5 at +20 · max 30',
    );
    expect(summary(preset({ stop_loss: null, breakeven_after: null }))).toBe('5 at +8, 5 at +20 · stop from the alert');
  });

  it('adds a stop to the alert when the preset has no backup stop', () => {
    expect(alertFor('nq split', preset())).toBe(
      '{"action": "{{strategy.order.action}}", "symbol": "{{ticker}}", "preset": "nq split"}',
    );
    expect(alertFor('nq split', preset({ stop_loss: null }))).toContain('"stop_loss": 10');
  });
});

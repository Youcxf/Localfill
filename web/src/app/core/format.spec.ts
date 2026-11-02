import { formatDuration, formatMoney, formatPrice, formatSigned, utcDateTime, utcTime } from './format';

describe('format', () => {
  it('shows prices with thousands and two decimals', () => {
    expect(formatPrice(20055.5)).toBe('20,055.50');
    expect(formatPrice(6005)).toBe('6,005.00');
  });

  it('always shows a sign, with a real minus', () => {
    expect(formatSigned(2)).toBe('+2');
    expect(formatSigned(-1)).toBe('−1');
    expect(formatSigned(3, 2)).toBe('+3.00');
  });

  it('shows money with a sign and a dollar', () => {
    expect(formatMoney(186)).toBe('+$186.00');
    expect(formatMoney(-137.5)).toBe('−$137.50');
    expect(formatMoney(-1234)).toBe('−$1,234.00');
  });

  it('keeps utc times as they come', () => {
    expect(utcTime('2026-09-20T21:46:57+00:00')).toBe('21:46:57');
    expect(utcTime(null)).toBe('');
    expect(utcDateTime('2026-09-20T21:02:44+00:00')).toBe('2026-09-20 21:02:44 UTC');
  });

  it('turns seconds into hours and minutes', () => {
    expect(formatDuration(4465)).toBe('1 h 14 min');
    expect(formatDuration(3120)).toBe('52 min');
    expect(formatDuration(30)).toBe('less than a minute');
    expect(formatDuration(-5)).toBe('less than a minute');
  });
});

// small helpers to show numbers and times the way the design does

const priceFormat = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// 20055.5 -> "20,055.50"
export function formatPrice(value: number): string {
  return priceFormat.format(value);
}

// always shows a sign, and uses a real minus (−) instead of a hyphen: 2 -> "+2", -1 -> "−1"
export function formatSigned(value: number, digits = 0): string {
  const sign = value < 0 ? '−' : '+';
  return sign + Math.abs(value).toFixed(digits);
}

// 186 -> "+$186.00", -137.5 -> "−$137.50"
export function formatMoney(value: number): string {
  const sign = value < 0 ? '−' : '+';
  return sign + '$' + priceFormat.format(Math.abs(value));
}

// "2026-09-20T21:46:57+00:00" -> "21:46:57" (the api sends utc, we show it as it is)
export function utcTime(iso: string | null): string {
  return iso ? iso.slice(11, 19) : '';
}

// "2026-09-20T21:02:44+00:00" -> "2026-09-20 21:02:44 UTC"
export function utcDateTime(iso: string): string {
  return iso.slice(0, 10) + ' ' + iso.slice(11, 19) + ' UTC';
}

// 4465 -> "1 h 14 min", 3120 -> "52 min"
export function formatDuration(seconds: number): string {
  const minutes = Math.floor(Math.max(0, seconds) / 60);
  if (minutes < 1) {
    return 'less than a minute';
  }
  if (minutes < 60) {
    return `${minutes} min`;
  }
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

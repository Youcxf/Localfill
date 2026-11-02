// turns a failed http call into a sentence we can show to the user

interface ApiProblem {
  loc?: (string | number)[];
  msg?: string;
}

export function errorText(error: unknown): string {
  const failure = error as { status?: number; error?: { detail?: unknown } };

  // status 0 means the request never reached the server
  if (failure?.status === 0) {
    return 'could not reach localfill, is it running?';
  }

  const detail = failure?.error?.detail;
  if (typeof detail === 'string') {
    return detail;
  }

  // fastapi sends a list of problems when the body does not fit, like "qty: must be at least 1"
  if (Array.isArray(detail)) {
    return (detail as ApiProblem[]).map((p) => `${(p.loc ?? []).slice(1).join('.')}: ${p.msg}`).join('; ');
  }

  return 'something went wrong';
}

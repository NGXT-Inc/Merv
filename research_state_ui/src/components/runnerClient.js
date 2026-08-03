/**
 * Loopback client for the merv-agent-runner settings service, shared by the
 * Auto running panel and its setup wizard. The service replies with
 * machine-readable error codes; translate the known ones.
 */

const RUNNER_ERRORS = {
  pairing_token_required: 'The runner rejected this pairing token.',
  origin_not_allowed: 'The runner does not allow this site; set MERV_AGENT_UI_ORIGINS on that machine.',
  forbidden: 'The runner refused the request.',
};

export function runnerBase(url) {
  const base = url.trim().replace(/\/+$/, '');
  const target = new URL(base);
  if (
    !['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname)
    || !['http:', 'https:'].includes(target.protocol)
    || target.username
    || target.password
  ) {
    throw new Error('Runner URL must be an explicit loopback HTTP address.');
  }
  return base;
}

export async function runnerRequest({ url, token, method = 'GET', path = '/settings', body }) {
  const response = await fetch(`${runnerBase(url)}${path}`, {
    method,
    credentials: 'omit',
    headers: {
      Authorization: `Bearer ${(token || '').trim()}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.error || payload?.detail || payload?.message;
    throw new Error(
      RUNNER_ERRORS[detail] || detail || `Runner returned HTTP ${response.status}`,
    );
  }
  return payload;
}

export function connectFailureMessage(error) {
  return error instanceof TypeError
    ? 'No settings service answered there. Start merv-agent-runner on that machine first.'
    : (error?.message || 'Could not connect to the local runner.');
}

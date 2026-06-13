/**
 * Thin fetch wrapper for the research_plugin HTTP API (UI_API.md).
 *
 * In dev, Vite proxies /api and /health to 127.0.0.1:8787. In production
 * the UI is intended to run alongside the backend on the same host; allow
 * an override via VITE_API_BASE.
 */
// Resolution order: build-time override, then a runtime dev override
// (localStorage 'rsui:apiBase' — handy for pointing the UI at a working-tree
// daemon on another port; the backend allows cross-origin reads), then the
// same-origin default (Vite dev proxy / production co-hosting).
const BASE =
  import.meta.env.VITE_API_BASE
  || (typeof localStorage !== 'undefined' && localStorage.getItem('rsui:apiBase'))
  || '';

// The UI build's wire version, stamped on every request as X-RP-Client-Version
// (the cloud control plane reads it for the compat handshake; local mode
// ignores it). Kept in lockstep with the research_plugin package version.
export const CLIENT_VERSION = '0.0008';

// Bearer token for the hosted control plane. Dormant in local mode: with no
// token configured no Authorization header is sent, and the local backend
// (auth=None) serves every request as the implicit local principal. Resolution
// mirrors BASE — build-time override, then a runtime override.
function authToken() {
  return (
    import.meta.env.VITE_API_TOKEN
    || (typeof localStorage !== 'undefined' && localStorage.getItem('rsui:apiToken'))
    || ''
  );
}

async function request(path, { method = 'GET', body, signal } = {}) {
  const init = { method, signal, headers: { 'X-RP-Client-Version': CLIENT_VERSION } };
  const token = authToken();
  if (token) init.headers['Authorization'] = `Bearer ${token}`;
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(`${BASE}${path}`, init);
  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = { raw: text }; }
  }
  if (!res.ok) {
    const err = new Error((data && (data.message || data.detail || data.error)) || `HTTP ${res.status} on ${method} ${path}`);
    err.status = res.status;
    err.data = data;
    // Typed codes for the two control-plane gates so callers can react
    // (login prompt / upgrade banner) instead of showing a raw HTTP error.
    // Both are inert in local mode, which never returns 401/426.
    if (res.status === 401) err.code = 'unauthorized';
    else if (res.status === 426) err.code = 'client_too_old';
    else if (data && data.error_code) err.code = data.error_code;
    throw err;
  }
  return data;
}

export const api = {
  // Server identity + compat floor (version handshake). Returns
  // { server_version, min_daemon_version, min_proxy_version }; identical in
  // every mode. Used to show the live backend version and an upgrade hint.
  getMeta: () => request('/api/meta'),

  // Projects
  listProjects: () => request('/api/projects'),
  createProject: ({ name, summary, repo_root }) => request('/api/projects', { method: 'POST', body: { name, summary: summary || '', repo_root } }),
  patchProject: (pid, patch) => request(`/api/projects/${encodeURIComponent(pid)}`, { method: 'PATCH', body: patch }),
  getHome: (pid, signal) => request(`/api/projects/${encodeURIComponent(pid)}/home`, { signal }),

  // Claims
  createClaim: (pid, { statement, scope, confidence }) =>
    request(`/api/projects/${encodeURIComponent(pid)}/claims`, {
      method: 'POST',
      body: { statement, scope: scope || '', confidence: confidence || 'medium' },
    }),
  getClaim: (pid, cid) => request(`/api/projects/${encodeURIComponent(pid)}/claims/${encodeURIComponent(cid)}`),

  // Experiments
  createExperiment: (pid, { name, intent, claim_ids }) =>
    request(`/api/projects/${encodeURIComponent(pid)}/experiments`, {
      method: 'POST',
      body: { name, intent, claim_ids: claim_ids || [] },
    }),
  getExperimentStatus: (pid, eid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/experiments/${encodeURIComponent(eid)}/status`),
  // Derived figure graph (nodes + edges) for the experiment canvas.
  getExperimentFigure: (pid, eid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/experiments/${encodeURIComponent(eid)}/figure`),
  // Agent-authored logic graph (role 'graph') + envelope lint problems.
  getExperimentLogicGraph: (pid, eid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/experiments/${encodeURIComponent(eid)}/graph`),
  transitionExperiment: (pid, eid, transition, evidence) =>
    request(`/api/projects/${encodeURIComponent(pid)}/experiments/${encodeURIComponent(eid)}/transition`, {
      method: 'POST',
      body: { transition, ...(evidence ? { evidence } : {}) },
    }),

  // Syntheses (project reflection waves)
  // List + staleness/coverage signal for the Home panel.
  getSyntheses: (pid, signal) =>
    request(`/api/projects/${encodeURIComponent(pid)}/syntheses`, { signal }),
  getSynthesis: (pid, sid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/syntheses/${encodeURIComponent(sid)}`),
  // The living project logic graph (same payload shape as the experiment one).
  getProjectLogicGraph: (pid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/syntheses/current/graph`),

  // Resources
  registerResource: (pid, { path, kind, title }) =>
    request(`/api/projects/${encodeURIComponent(pid)}/resources`, {
      method: 'POST',
      body: { path, kind, ...(title ? { title } : {}) },
    }),
  associateResource: (pid, rid, { target_type, target_id, role }) =>
    request(`/api/projects/${encodeURIComponent(pid)}/resources/${encodeURIComponent(rid)}/associate`, {
      method: 'POST',
      body: { target_type, target_id, role },
    }),
  deleteResource: (pid, rid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/resources/${encodeURIComponent(rid)}`, {
      method: 'DELETE',
    }),
  getResourceContent: (pid, rid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/resources/${encodeURIComponent(rid)}/content`),
  // rel: optional path relative to the resource's own directory (locked inside
  // the repo root server-side) — used to resolve a report's figure links.
  resourceFileUrl: (pid, rid, rel = null) =>
    `${BASE}/api/projects/${encodeURIComponent(pid)}/resources/${encodeURIComponent(rid)}/file${
      rel ? `?rel=${encodeURIComponent(rel)}` : ''
    }`,

  // Version history. Resources carry version metadata directly
  // (current_version_id, associations[].version_id); `history` returns version
  // metadata only (sha256, size, mtime, content_type) — the backend does not
  // store or serve historical file content.
  getResourceHistory: (pid, rid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/resources/${encodeURIComponent(rid)}/history`),

  // Reviews
  listReviews: (pid, target = {}) => {
    const params = new URLSearchParams();
    if (target.target_type) params.set('target_type', target.target_type);
    if (target.target_id) params.set('target_id', target.target_id);
    const q = params.toString();
    return request(`/api/projects/${encodeURIComponent(pid)}/reviews${q ? '?' + q : ''}`);
  },
  // Events
  listEvents: (pid, limit = 100) =>
    request(`/api/projects/${encodeURIComponent(pid)}/events?limit=${limit}`),

  // Activity — workspace-scoped (http.request + tool.call telemetry).
  // Returns { activity_log, events, summary } oldest-first. The optional
  // `source` filter is applied server-side BEFORE `limit` so a request for
  // 300 mcp events returns 300 mcp events, not 300 mixed events of which mcp
  // is a sliver.
  listActivity: (limit = 200, source = null) => {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    if (source && source !== 'all') params.set('source', source);
    return request(`/api/activity?${params.toString()}`);
  },

  // Tool-call I/O analyzer. Per-tool aggregate (avg/p50/p95/max received) plus a
  // filtered, sortable slice of individual calls — each drillable to its full
  // raw request/response via getToolCall(). Powers the Debug page.
  toolCallStats: ({ minutes, source, status, tool, limit = 300, sort = 'ts', order = 'desc' } = {}) => {
    const p = new URLSearchParams();
    if (minutes) p.set('minutes', String(minutes));
    if (source && source !== 'all') p.set('source', source);
    if (status && status !== 'all') p.set('status', status);
    if (tool) p.set('tool', tool);
    p.set('limit', String(limit));
    p.set('sort', sort);
    p.set('order', order);
    return request(`/api/debug/tool-calls?${p.toString()}`);
  },
  // Full raw record for one call, with args/result parsed back to native JSON.
  getToolCall: (id) => request(`/api/debug/tool-calls/${encodeURIComponent(id)}`),
  clearToolCalls: () => request(`/api/debug/tool-calls/clear`, { method: 'POST' }),

  // Sandboxes (cloud-backed; agent drives execution over SSH — see sandboxes.py).
  // The UI observes; it does not procure sandboxes (that is an agent MCP action).
  listSandboxes: (pid) => request(`/api/projects/${encodeURIComponent(pid)}/sandboxes`),
  getSandbox: (pid, eid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/experiments/${encodeURIComponent(eid)}/sandbox`),
  // Terminal transcript. Pass { since: cursor } (from the previous response's
  // `cursor`) to fetch only new bytes — the cheap incremental poll. Without
  // `since`, returns the last `tail` bytes (the initial full pull).
  getSandboxTerminal: (pid, eid, { tail = 200000, since = null } = {}) => {
    const p = new URLSearchParams();
    if (since != null) p.set('since', String(since));
    else p.set('tail', String(tail));
    return request(
      `/api/projects/${encodeURIComponent(pid)}/experiments/${encodeURIComponent(eid)}/sandbox/terminal?${p.toString()}`,
    );
  },
  // Live in-container usage (CPU/RAM/GPU), sampled on demand. Best-effort:
  // returns { available: false } when the sandbox is not running or the sampler
  // came back empty (e.g. a CPU-only image without nvidia-smi).
  getSandboxMetrics: (pid, eid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/experiments/${encodeURIComponent(eid)}/sandbox/metrics`),
  // Durable archived MLflow metrics that OUTLIVE the sandbox VM (captured on
  // sync and right before release, recorded control-plane side). Distinct from
  // getSandboxMetrics (live in-container CPU/RAM/GPU, gone once the VM stops).
  // Returns { available, sandbox_status, experiments:[{name, runs:[...]}], hint? }.
  getResultsMetrics: (pid, eid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/experiments/${encodeURIComponent(eid)}/results/metrics`),
  syncSandbox: (pid, eid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/experiments/${encodeURIComponent(eid)}/sandbox/sync`, { method: 'POST' }),
  releaseSandbox: (pid, eid) =>
    request(`/api/projects/${encodeURIComponent(pid)}/experiments/${encodeURIComponent(eid)}/sandbox/release`, { method: 'POST' }),
};

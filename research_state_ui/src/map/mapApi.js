// Research Map transport. The snapshot is server-rendered pixels (the same
// renderer the agent tools consume — pixel parity); it is fetched as an authed
// blob because hosted mode serves bytes behind the Bearer token (see api.js).
import { request, fetchObjectUrl } from '../api';

const base = (pid) => `/api/projects/${encodeURIComponent(pid)}/map`;

export function getMapState(projectId, { signal } = {}) {
  return request(`${base(projectId)}/state`, { signal });
}

// Returns an object URL; caller MUST URL.revokeObjectURL when done.
export function fetchSnapshotUrl(projectId, { cx, cy, zoom, w, h, scale, signal }) {
  const q = new URLSearchParams({
    cx: String(cx), cy: String(cy), zoom: String(zoom),
    w: String(Math.round(w)), h: String(Math.round(h)), scale: String(scale),
  });
  return fetchObjectUrl(`${base(projectId)}/snapshot?${q}`, { signal });
}

export function pinEntity(projectId, entityId, x, y) {
  return request(`${base(projectId)}/pin`, {
    method: 'POST', body: { entity_id: entityId, x, y },
  });
}

export function unpinEntity(projectId, entityId) {
  return request(`${base(projectId)}/unpin`, {
    method: 'POST', body: { entity_id: entityId },
  });
}

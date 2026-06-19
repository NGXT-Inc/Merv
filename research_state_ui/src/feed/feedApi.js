/**
 * Feed HTTP endpoints (Feed_PRD.md). Self-contained: the feed owns its endpoint
 * definitions and reuses only the shared transport (`request`, `mediaUrl`) from
 * api.js — so nothing in the core api client depends on the feed.
 */
import { request, mediaUrl } from '../api';

export const feedApi = {
  // Reverse-chronological posts; `cursor` is the created_seq of the last item
  // from the previous page (infinite scroll). The first page (no cursor) also
  // carries a soft posting `nudge` for agents — harmless/ignored by the UI.
  getFeed: (pid, { limit = 30, cursor = null, signal } = {}) => {
    const p = new URLSearchParams();
    p.set('limit', String(limit));
    if (cursor != null) p.set('cursor', String(cursor));
    return request(`/api/projects/${encodeURIComponent(pid)}/feed?${p.toString()}`, { signal });
  },
  // Absolute URL for a server-provided media path (post image / link thumbnail).
  mediaUrl,
  // Usage analytics (fire-and-forget): feed_opened / post_viewed / link_clicked.
  trackFeed: (pid, event, extra = {}) =>
    request(`/api/projects/${encodeURIComponent(pid)}/feed/track`, {
      method: 'POST',
      body: { event, ...extra },
    }),
};

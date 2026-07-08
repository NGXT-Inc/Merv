// Presentation model for the feed's sense of time. One shared clock drives
// every relative timestamp so the whole page ages in step (and stale "2m ago"
// labels can't linger), and day dividers give the stream a calendar rhythm.
import { useEffect, useState } from 'react';
import { fmtAgo } from '../utils/format';

// Shared ticking clock. One instance lives in Feed and flows down as a prop.
export function useNow(intervalMs = 30000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function dayKey(ts) {
  const d = new Date(ts);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

// Calendar-aware (setDate handles DST days that aren't 24h long).
function yesterdayKey(now) {
  const d = new Date(now);
  d.setDate(d.getDate() - 1);
  return dayKey(d);
}

export function dayLabel(ts, now) {
  if (dayKey(ts) === dayKey(now)) return 'Today';
  if (dayKey(ts) === yesterdayKey(now)) return 'Yesterday';
  const d = new Date(ts);
  const sameYear = d.getFullYear() === new Date(now).getFullYear();
  return d.toLocaleDateString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  });
}

// A post's timestamp: relative while it is from today ("5m ago"); on older
// days the divider already names the date, so just the clock time ("2:05 PM").
export function postTime(ts, now) {
  if (ts == null || !Number.isFinite(ts)) return '';
  if (dayKey(ts) === dayKey(now)) return fmtAgo(now - ts);
  return new Date(ts).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

// Interleave day dividers into a newest-first post list. The leading "Today"
// divider is skipped (a feed that opens on today needs no announcement); any
// other day change gets one, including a non-today first group.
//
// lastSeenSeq (optional) marks where the previous visit ended: one `unseen`
// item lands between the newest already-seen post and everything above it.
// No marker when nothing is new, or when nothing was seen before (first visit).
// A post joins the one above it (same author, posted within this window) into
// a visual run: the newest keeps the byline, continuations drop it.
const GROUP_WINDOW_MS = 20 * 60_000;

// Thread the flat newest-first list: replies group directly under the post
// they answer, roots keep their reverse-chron order. One level of nesting only
// — a reply to a reply attaches to the thread's root (depth stays 1), replies
// under a root read oldest-first (conversation order). A reply whose parent
// isn't in the loaded window stands alone as an `orphan` root (the card shows
// a small "replying to an earlier post" line instead of silently flattening).
export function threadPosts(posts) {
  const byId = new Map(posts.map((p) => [p.id, p]));
  const children = new Map();
  const roots = [];
  for (const post of posts) {
    if (post.in_reply_to && byId.has(post.in_reply_to)) {
      // Walk to the thread root (guarded — a malformed cycle must not hang).
      let root = byId.get(post.in_reply_to);
      let guard = 0;
      while (root.in_reply_to && byId.has(root.in_reply_to) && guard++ < 50) {
        root = byId.get(root.in_reply_to);
      }
      if (root.id !== post.id) {
        if (!children.has(root.id)) children.set(root.id, []);
        children.get(root.id).push(post);
        continue;
      }
    }
    roots.push(post);
  }
  const out = [];
  for (const root of roots) {
    out.push({
      post: root,
      depth: 0,
      orphan: Boolean(root.in_reply_to && !byId.has(root.in_reply_to)),
    });
    const kids = children.get(root.id);
    if (kids) {
      kids.sort((a, b) => (a.created_seq || 0) - (b.created_seq || 0));
      for (const kid of kids) out.push({ post: kid, depth: 1 });
    }
  }
  return out;
}

export function withDayDividers(posts, now, lastSeenSeq = null) {
  const threaded = threadPosts(posts);
  const items = [];
  let prevKey = dayKey(now);
  let prevPost = null; // previous post item, reset by any divider between
  let unseenPlaced = lastSeenSeq == null || (posts.length > 0 && posts[0].created_seq <= lastSeenSeq);
  for (const { post, depth, orphan } of threaded) {
    // Replies live inside their parent's block: no dividers or markers land
    // between a post and its thread, and a thread interrupts continuation runs.
    if (depth > 0) {
      items.push({ type: 'post', id: post.id, post, grouped: false, depth });
      prevPost = null;
      continue;
    }
    if (!unseenPlaced && post.created_seq <= lastSeenSeq) {
      items.push({ type: 'unseen', id: 'unseen' });
      unseenPlaced = true;
      prevPost = null;
    }
    const ts = post.created_at ? Date.parse(post.created_at) : NaN;
    if (Number.isFinite(ts)) {
      const key = dayKey(ts);
      if (key !== prevKey) {
        // Key includes the post id: agent clock skew can interleave days
        // within seq order, and duplicate keys would break reconciliation.
        items.push({ type: 'day', id: `day-${key}-${post.id}`, ts });
        prevKey = key;
        prevPost = null;
      }
    }
    // The list is newest-first, so `prevPost` (above on screen) is the newer
    // one; this post continues its run when the same author posted both
    // within the window.
    let grouped = false;
    if (prevPost && prevPost.post.author_handle === post.author_handle) {
      const prevTs = Date.parse(prevPost.post.created_at);
      grouped = Number.isFinite(ts) && Number.isFinite(prevTs) && prevTs - ts <= GROUP_WINDOW_MS;
    }
    const item = { type: 'post', id: post.id, post, grouped, depth: 0, orphan };
    items.push(item);
    prevPost = item;
  }
  // Everything loaded is new (the boundary post is beyond this page): close
  // the list with the marker so the heaviest backlog still gets its signal.
  if (!unseenPlaced && posts.length) items.push({ type: 'unseen', id: 'unseen' });
  return items;
}

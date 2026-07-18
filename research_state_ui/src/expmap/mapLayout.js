/**
 * mapLayout — pure layout math for the Experiment Map.
 *
 * Ported from the design-handoff prototype (design_handoff_experiment_map/
 * "Experiment Map.dc.html"): a gap-compressed piecewise-linear time axis,
 * greedy row packing, per-day axis ticks, and the clamped "now" line.
 * Pure data → data; no React, no fetches, unit-testable with node.
 */

export const HOUR = 6;      // px per hour along the time axis
export const GAP_H = 30;    // gaps longer than this many hours compress
export const BREAK_W = 90;  // px a compressed gap collapses to
export const CARD_W = 284;
export const CARD_H = 122;
export const ROW_H = 200;

/**
 * Gap-compressed time scale over experiment start times.
 * Sorted unique times become piecewise-linear anchors; a gap > GAP_H hours
 * occupies a fixed BREAK_W px. Returns { anchors, xFor }; xFor interpolates
 * between anchors and extrapolates past the last one at HOUR px/h.
 */
export function buildTimeScale(startTimesMs) {
  const times = [...new Set(startTimesMs)].filter(Number.isFinite).sort((a, b) => a - b);
  if (times.length === 0) return { anchors: [], xFor: () => 0 };
  const anchors = [{ t: times[0], x: 0 }];
  for (let i = 1; i < times.length; i++) {
    const dtH = (times[i] - times[i - 1]) / 3600000;
    const prev = anchors[anchors.length - 1];
    anchors.push({ t: times[i], x: prev.x + (dtH > GAP_H ? BREAK_W : dtH * HOUR) });
  }
  const xFor = (t) => {
    if (t <= anchors[0].t) return anchors[0].x;
    for (let i = 1; i < anchors.length; i++) {
      if (t <= anchors[i].t) {
        const a = anchors[i - 1];
        const b = anchors[i];
        return a.x + ((t - a.t) / (b.t - a.t)) * (b.x - a.x);
      }
    }
    const la = anchors[anchors.length - 1];
    return la.x + ((t - la.t) / 3600000) * HOUR; // extrapolate past the last event
  };
  return { anchors, xFor };
}

/**
 * Greedy row packing: sort by x, take the first row whose last occupant's
 * right edge sits more than 28px left of the new x; y = row × ROW_H.
 * items = [{ id, x }] → { id: { x, y } }.
 */
export function packRows(items) {
  const pos = {};
  const rows = [];
  const all = items.slice().sort((a, b) => a.x - b.x);
  for (const { id, x } of all) {
    let row = rows.findIndex((end) => x > end + 28);
    if (row === -1) { row = rows.length; rows.push(0); }
    rows[row] = x + CARD_W;
    pos[id] = { x, y: row * ROW_H };
  }
  return pos;
}

const p2 = (n) => String(n).padStart(2, '0');
// Local-time day key, matching the local-time labels the cards render.
const dayKey = (ms) => {
  const d = new Date(ms);
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
};

/**
 * One axis tick per distinct start day, at that day's earliest event x − 24.
 * Wide label ("Thu, Jul 2") when there's > 110px of room since the previous
 * label's end, tight ("Jul 2") otherwise. World coordinates — screen-space
 * culling belongs to the view.
 */
export function dayTicks(experiments, xFor) {
  const byDay = {};
  for (const e of experiments) {
    if (!Number.isFinite(e.startMs)) continue;
    const day = dayKey(e.startMs);
    if (!(day in byDay) || e.startMs < byDay[day]) byDay[day] = e.startMs;
  }
  let lastEnd = -Infinity;
  return Object.entries(byDay).sort((a, b) => a[1] - b[1]).map(([day, t]) => {
    const x = Math.round(xFor(t)) - 24;
    const wide = x - lastEnd > 110;
    const label = new Date(`${day}T12:00`).toLocaleDateString(
      'en-US',
      wide ? { weekday: 'short', month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric' },
    );
    lastEnd = x + label.length * 7.5;
    return { x, label };
  });
}

// The now line: nothing may sit to its right, so clamp past the rightmost
// card's right edge + 48px even when wall-clock time maps further left.
export function nowX(xFor, positions, nowMs) {
  const xs = Object.values(positions).map((p) => p.x);
  const rightMost = (xs.length ? Math.max(...xs) : 0) + CARD_W;
  return Math.round(Math.max(xFor(nowMs), rightMost + 48));
}

/**
 * Composed layout for the map. cards = [{ id, startMs }] →
 * { pos, ticks, nowX, bounds, xFor }; bounds covers the card rects.
 * (xFor is included beyond the base contract so the view can refresh the
 * cheap now-clamp without re-packing.)
 */
export function computeLayout(cards, nowMs) {
  const scale = buildTimeScale(cards.map((c) => c.startMs));
  const pos = packRows(cards.map((c) => ({ id: c.id, x: Math.round(scale.xFor(c.startMs)) })));
  const ticks = dayTicks(cards, scale.xFor);
  const nx = nowX(scale.xFor, pos, nowMs);
  let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity;
  for (const p of Object.values(pos)) {
    if (p.x < minX) minX = p.x;
    if (p.x + CARD_W > maxX) maxX = p.x + CARD_W;
    if (p.y < minY) minY = p.y;
    if (p.y + CARD_H > maxY) maxY = p.y + CARD_H;
  }
  if (!Number.isFinite(minX)) { minX = 0; maxX = 0; minY = 0; maxY = 0; }
  return { pos, ticks, nowX: nx, bounds: { minX, maxX, minY, maxY }, xFor: scale.xFor };
}

/**
 * field.js — the backdrop universe, pure model (no DOM, node-testable).
 *
 * Grammar borrowed from the merv landing hero (WireframeSolid): ONE constant,
 * instantly-legible geometric form — a wireframe platonic solid — rotating
 * slowly in 3D. Nothing about the geometry ever develops; all life is the
 * depth-pulse of edges swinging near and away, plus:
 *
 *   project → which solid, its size, placement, spin axis/rate, accent tint,
 *             and a sparse dust layer behind it. Deterministic per project.
 *   route   → a whisper: the spin axis re-tilts a few degrees and the solid
 *             drifts to a new horizontal home, both eased over seconds. No
 *             shape churn on navigation.
 *   scroll  → angular momentum: scrolling kicks the spin (with inertia),
 *             which damps back to the idle rate when the reader rests.
 *
 * All randomness is seeded (xmur3 → mulberry32), so a given project always
 * gets the same universe.
 */

const TAU = Math.PI * 2;

// xmur3: string → well-mixed 32-bit seed.
export function hashSeed(str) {
  let h = 1779033703 ^ str.length;
  for (let i = 0; i < str.length; i++) {
    h = Math.imul(h ^ str.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  h = Math.imul(h ^ (h >>> 16), 2246822507);
  h = Math.imul(h ^ (h >>> 13), 3266489909);
  return (h ^ (h >>> 16)) >>> 0;
}

// mulberry32: seed → deterministic PRNG in [0, 1).
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const pick = (rng, weights) => {
  let r = rng() * weights.reduce((s, w) => s + w, 0);
  for (let i = 0; i < weights.length; i++) { r -= weights[i]; if (r < 0) return i; }
  return weights.length - 1;
};

// Verdict accents a project can carry; the dominant one tints near edges.
const ACCENTS = ['active', 'ice', 'supports', 'refutes', 'qualifies'];

/* ---- Polyhedra: vertices on the unit sphere, edges = shortest-pair rule -- */

const PHI = (1 + Math.sqrt(5)) / 2;

const norm = (vs) => vs.map(([x, y, z]) => {
  const m = Math.hypot(x, y, z);
  return [x / m, y / m, z / m];
});

// Every solid here has a single edge length, so nearest-pairs finds exactly
// the true edges.
const edgesOf = (verts) => {
  let min = Infinity;
  for (let i = 0; i < verts.length; i++) {
    for (let j = i + 1; j < verts.length; j++) {
      const d = Math.hypot(verts[i][0] - verts[j][0], verts[i][1] - verts[j][1], verts[i][2] - verts[j][2]);
      if (d < min) min = d;
    }
  }
  const edges = [];
  for (let i = 0; i < verts.length; i++) {
    for (let j = i + 1; j < verts.length; j++) {
      const d = Math.hypot(verts[i][0] - verts[j][0], verts[i][1] - verts[j][1], verts[i][2] - verts[j][2]);
      if (Math.abs(d - min) < 0.001) edges.push([i, j]);
    }
  }
  return edges;
};

const RAW = {
  octa: [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]],
  cubocta: [
    [1, 1, 0], [1, -1, 0], [-1, 1, 0], [-1, -1, 0],
    [1, 0, 1], [1, 0, -1], [-1, 0, 1], [-1, 0, -1],
    [0, 1, 1], [0, 1, -1], [0, -1, 1], [0, -1, -1],
  ],
  icosa: [
    [-1, PHI, 0], [1, PHI, 0], [-1, -PHI, 0], [1, -PHI, 0],
    [0, -1, PHI], [0, 1, PHI], [0, -1, -PHI], [0, 1, -PHI],
    [PHI, 0, -1], [PHI, 0, 1], [-PHI, 0, -1], [-PHI, 0, 1],
  ],
  dodeca: [
    [1, 1, 1], [1, 1, -1], [1, -1, 1], [1, -1, -1],
    [-1, 1, 1], [-1, 1, -1], [-1, -1, 1], [-1, -1, -1],
    [0, 1 / PHI, PHI], [0, 1 / PHI, -PHI], [0, -1 / PHI, PHI], [0, -1 / PHI, -PHI],
    [1 / PHI, PHI, 0], [1 / PHI, -PHI, 0], [-1 / PHI, PHI, 0], [-1 / PHI, -PHI, 0],
    [PHI, 0, 1 / PHI], [PHI, 0, -1 / PHI], [-PHI, 0, 1 / PHI], [-PHI, 0, -1 / PHI],
  ],
};

export const SOLIDS = Object.fromEntries(
  Object.entries(RAW).map(([k, raw]) => {
    const verts = norm(raw);
    return [k, { verts, edges: edgesOf(verts) }];
  })
);

const SOLID_KEYS = ['octa', 'cubocta', 'icosa', 'dodeca'];
const SOLID_WEIGHTS = [0.15, 0.2, 0.35, 0.3];

/** Project signature: which solid, where, how it spins, how warm it runs. */
export function projectCharacter(projectKey) {
  const rng = mulberry32(hashSeed(`solid::${projectKey}`));
  const accentWeights = ACCENTS.map(() => (rng() < 0.5 ? 0.15 : rng() * 1.6 + 0.2));
  const accent = ACCENTS[accentWeights.indexOf(Math.max(...accentWeights))];
  const dustRng = mulberry32(hashSeed(`dust::${projectKey}`));
  return {
    solid: SOLID_KEYS[pick(rng, SOLID_WEIGHTS)],
    radiusF: 0.34 + rng() * 0.14,               // of min(viewport w, h)
    // Height is project identity; the horizontal home comes from routePose —
    // each screen parks the solid somewhere else. cx0 only seeds pre-route state.
    cx0: 0.24 + rng() * 0.52,
    cy: 0.34 + rng() * 0.3,
    // Idle spin (rad/s): a full turn every ~30-60s, seeded direction.
    wy: (0.1 + rng() * 0.12) * (rng() < 0.5 ? -1 : 1),
    wxr: 0.45 + rng() * 0.5,                    // x-axis rate as a fraction of wy
    phase0: rng() * TAU,                        // starting orientation
    accent,
    accentAmt: 0.5 + rng() * 0.4,               // how orange/green/blue it runs near
    // A handful of static bokeh motes for depth; they parallax, never rewire.
    dust: Array.from({ length: 14 + Math.floor(dustRng() * 10) }, () => ({
      x: dustRng(), y: dustRng(),
      r: 1.4 + dustRng() * 1.8,
      rate: 0.015 + dustRng() * 0.04,           // parallax fraction of scroll
      phase: dustRng() * TAU,
      a: 0.2 + dustRng() * 0.25,
      tone: dustRng() < 0.12 ? accent : 'faint',
    })),
  };
}

/** Route whisper: per project+route, a small spin-axis tilt and a horizontal
 *  home for the solid — different screens park it in different places. */
export function routePose(projectKey, routeKey) {
  const rng = mulberry32(hashSeed(`tilt::${projectKey}::${routeKey}`));
  return {
    tx: (rng() - 0.5) * 0.7,
    ty: (rng() - 0.5) * 0.7,
    cx: 0.24 + rng() * 0.52,
  };
}

// Scroll → angular momentum: gain, terminal cap, and how fast it damps back.
const KICK_GAIN = 0.9;
const KICK_MAX = 2.2;
const KICK_DAMP = 1.3;
const TILT_EASE = 0.7; // per-second exponential approach to the route tilt
const DRIFT_EASE = 0.45; // slower still: the horizontal relocation glide

/**
 * createField(projectKey) → live universe.
 *   setScene(routeKey)  retarget the axis tilt (eases in over seconds)
 *   step(dt, scrollVel) integrate spin; returns unrest energy for loop gating
 *   ax()/ay()           current rotation angles for the renderer
 */
export function createField(projectKey) {
  const character = projectCharacter(projectKey);
  const solid = SOLIDS[character.solid];
  const wy = character.wy;
  const wx = character.wy * character.wxr;
  let axBase = character.phase0 * 0.7 + 0.5;
  let ayBase = character.phase0;
  let offX = 0, offY = 0;   // eased axis tilt
  let tiltX = 0, tiltY = 0; // its target, from the route
  let cx = character.cx0;   // eased horizontal home
  let cxT = character.cx0;  // its target, from the route
  let kick = 0;             // scroll-injected angular velocity
  let routeKey = null;

  function setScene(nextRoute) {
    if (nextRoute === routeKey) return;
    const first = routeKey === null;
    routeKey = nextRoute;
    const p = routePose(projectKey, nextRoute);
    tiltX = p.tx;
    tiltY = p.ty;
    cxT = p.cx;
    if (first) { offX = tiltX; offY = tiltY; cx = cxT; } // first paint: settled
  }

  function step(dt, scrollVel = 0) {
    // Axis tilt and horizontal home ease toward the route's pose.
    const k = Math.min(1, dt * TILT_EASE);
    offX += (tiltX - offX) * k;
    offY += (tiltY - offY) * k;
    // Exponential ease with a small linear floor, so the glide actually
    // lands (a pure exponential tail would idle at 60fps for ~20s).
    const dcx = cxT - cx;
    const stepMag = Math.max(Math.abs(dcx) * Math.min(1, dt * DRIFT_EASE), 0.008 * dt);
    cx += Math.sign(dcx) * Math.min(Math.abs(dcx), stepMag);
    // Scroll pumps momentum into the spin; inertia carries, damping settles.
    kick = Math.max(-KICK_MAX, Math.min(KICK_MAX, kick + scrollVel * KICK_GAIN * dt));
    kick *= Math.exp(-KICK_DAMP * dt);
    // Zero-snap the exponential tails so unrest actually reaches 0 and the
    // frame loop can drop back to its ambient cadence.
    if (Math.abs(kick) < 1e-4) kick = 0;
    if (Math.abs(tiltX - offX) < 1e-4) offX = tiltX;
    if (Math.abs(tiltY - offY) < 1e-4) offY = tiltY;
    axBase += (wx + kick) * dt;
    ayBase += (wy + kick * 0.35) * dt;
    return Math.abs(kick) + Math.abs(tiltX - offX) + Math.abs(tiltY - offY) + Math.abs(cxT - cx);
  }

  return {
    character,
    verts: solid.verts,
    edges: solid.edges,
    setScene,
    step,
    ax: () => axBase + offX,
    ay: () => ayBase + offY,
    cx: () => cx,
    kick: () => kick,
  };
}

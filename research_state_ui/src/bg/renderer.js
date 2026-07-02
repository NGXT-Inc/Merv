/**
 * renderer.js — paints a field (see field.js) onto a fixed canvas.
 *
 * One slowly tumbling wireframe solid over a sparse dust layer. The pulse is
 * the rotation itself: edges are painter-sorted by depth and their opacity,
 * width and warmth ride it — each edge inhales brightness as it swings near
 * and exhales as it recedes (the merv WireframeSolid grammar). Strokes are
 * two-pass soft (wide faint + narrow core) so they survive the CSS glass
 * blur; the solid parallax-drifts gently with scroll, the dust more so.
 */

// Perspective distance (unit-sphere space) — merv's gentle setting.
const PERSP = 2.6;
const SPRITE_SIZE = 64;

// Read the theme palette as "r, g, b" strings straight from the CSS vars.
export function readPalette() {
  const cs = getComputedStyle(document.documentElement);
  const rgb = (name) => {
    const triplet = cs.getPropertyValue(`--${name}-rgb`).trim();
    if (triplet) return triplet;
    const hex = cs.getPropertyValue(`--${name}`).trim();
    const m = /^#([0-9a-f]{6})$/i.exec(hex);
    if (!m) return '154, 156, 159';
    const v = parseInt(m[1], 16);
    return `${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}`;
  };
  return Object.fromEntries(
    ['active', 'ice', 'supports', 'refutes', 'qualifies', 'faint'].map(t => [t, rgb(t)])
  );
}

// Lerp two "r, g, b" strings.
const mixRgb = (a, b, t) => {
  const pa = a.split(',').map(Number), pb = b.split(',').map(Number);
  return pa.map((v, i) => Math.round(v + (pb[i] - v) * t)).join(', ');
};

// Soft radial glow sprites for dust motes (accents pre-washed toward faint —
// dust is texture, not markers).
function buildSprites(palette) {
  const sprites = {};
  for (const [tone, raw] of Object.entries(palette)) {
    const rgb = tone === 'faint' ? raw : mixRgb(raw, palette.faint, 0.55);
    const c = document.createElement('canvas');
    c.width = c.height = SPRITE_SIZE;
    const g = c.getContext('2d');
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, `rgba(${rgb}, 0.5)`);
    grad.addColorStop(0.35, `rgba(${rgb}, 0.18)`);
    grad.addColorStop(1, `rgba(${rgb}, 0)`);
    g.fillStyle = grad;
    g.fillRect(0, 0, SPRITE_SIZE, SPRITE_SIZE);
    sprites[tone] = c;
  }
  return sprites;
}

export function createRenderer(canvas) {
  const ctx = canvas.getContext('2d');
  let palette = readPalette();
  let sprites = buildSprites(palette);
  let w = 0, h = 0, dpr = 1;

  function resize() {
    dpr = Math.min(2, window.devicePixelRatio || 1);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function setPalette() {
    palette = readPalette();
    sprites = buildSprites(palette);
  }

  /**
   * view: { scrollY, t (seconds), still }
   * `still` freezes dust breathing (reduced motion / idle single paint).
   */
  function draw(field, view) {
    const { scrollY, t, still } = view;
    ctx.clearRect(0, 0, w, h);
    const c = field.character;

    // Dust: static motes that parallax with scroll (wrapping) and breathe.
    for (const d of c.dust) {
      const y = (((d.y - (scrollY * d.rate) / h) % 1) + 1) % 1;
      const breath = still ? 0.8 : 0.65 + 0.35 * Math.sin(t * 0.4 + d.phase);
      const size = d.r * 8;
      ctx.globalAlpha = d.a * breath;
      ctx.drawImage(sprites[d.tone], d.x * w - size / 2, y * h - size / 2, size, size);
    }
    ctx.globalAlpha = 1;

    // The solid: project, depth-sort, stroke back-to-front.
    const R = c.radiusF * Math.min(w, h);
    const cx = field.cx() * w; // eased per-route horizontal home
    // Gentle, bounded parallax: the solid rides up as the page scrolls.
    const cy = (c.cy - Math.tanh(scrollY / (h * 2.5)) * 0.1) * h;
    const ax = field.ax(), ay = field.ay();
    const cosy = Math.cos(ay), siny = Math.sin(ay);
    const cosx = Math.cos(ax), sinx = Math.sin(ax);
    const pts = field.verts.map(([vx, vy, vz]) => {
      const x = vx * cosy - vz * siny;
      let z = vx * siny + vz * cosy;
      const y = vy * cosx - z * sinx;
      z = vy * sinx + z * cosx;
      const persp = PERSP / (PERSP - z);
      return { x: cx + x * R * persp, y: cy + y * R * persp, z };
    });

    const order = field.edges
      .map(([i, j]) => ({ i, j, mz: (pts[i].z + pts[j].z) / 2 }))
      .sort((a, b) => a.mz - b.mz);

    ctx.lineCap = 'round';
    for (const e of order) {
      const a = pts[e.i], b = pts[e.j];
      const depth = (e.mz + 1) / 2; // 0 far .. 1 near
      const alpha = 0.06 + depth * 0.3;
      // Near edges warm toward the project accent; far edges stay grey.
      const warm = Math.max(0, depth - 0.5) / 0.5;
      const tone = warm > 0 ? mixRgb(palette.faint, palette[c.accent], warm * c.accentAmt * 0.8) : palette.faint;
      const width = 1 + depth * 1.9;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.strokeStyle = `rgba(${tone}, ${alpha * 0.35})`;
      ctx.lineWidth = width * 2.6;
      ctx.stroke();
      ctx.strokeStyle = `rgba(${tone}, ${alpha})`;
      ctx.lineWidth = width;
      ctx.stroke();
    }

    // Vertices: quiet joints, not nodes — small, depth-faded, softly warm.
    for (const p of pts) {
      const depth = (p.z + 1) / 2;
      const warm = Math.max(0, depth - 0.5) / 0.5;
      const tone = warm > 0 ? mixRgb(palette.faint, palette[c.accent], warm * c.accentAmt * 0.8) : palette.faint;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 1.3 + depth * 2.3, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${tone}, ${0.12 + depth * 0.28})`;
      ctx.fill();
    }
  }

  resize();
  return { resize, setPalette, draw };
}

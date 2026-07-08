// Stable visual identity for feed authors: a handle hashes to a hue, and the
// stylesheet owns lightness/chroma per theme (a fixed-L color that reads on
// the dark canvas disappears on the light one). The hash is avalanched so
// near-identical handles ("agent-1"/"agent-2") still land far apart.

export function authorHue(handle) {
  let h = 0;
  for (const ch of String(handle || '')) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  h ^= h >>> 16;
  h = Math.imul(h, 0x45d9f3bb) >>> 0;
  h ^= h >>> 16;
  return h % 360;
}

// Independent hash stream for avatar geometry (different mix than the hue
// hash, so shape and color never correlate across handles).
function shapeBits(handle) {
  let h = 0x811c9dc5;
  for (const ch of String(handle || '')) h = Math.imul(h ^ ch.codePointAt(0), 0x01000193) >>> 0;
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d) >>> 0;
  h ^= h >>> 12;
  return h >>> 0;
}

// Deterministic identicon geometry for a handle: one primary shape (rotated
// square / circle / semicircular arc) plus one small satellite mark, both
// placed from hash bits. Pure data — the Avatar component renders it and the
// stylesheet owns all color (hue via --author-hue, lightness per theme).
export function avatarSpec(handle) {
  const bits = shapeBits(handle);
  return {
    kind: bits % 3, // 0 rotated square, 1 circle, 2 arc
    rotation: ((bits >>> 2) % 8) * 45,
    size: 8 + ((bits >>> 5) % 3), // primary half-extent, 8..10
    dx: ((bits >>> 7) % 3) - 1, // primary center jitter, -1..1
    dy: ((bits >>> 9) % 3) - 1,
    satCorner: (bits >>> 11) % 4, // 0 tl, 1 tr, 2 br, 3 bl
    satRound: ((bits >>> 13) & 1) === 1, // satellite: circle or square
  };
}

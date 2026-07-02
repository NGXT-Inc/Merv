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

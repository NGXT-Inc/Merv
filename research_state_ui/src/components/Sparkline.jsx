/**
 * Sparkline — a dependency-free SVG polyline for a metric series. The answer to
 * "watch the loss/accuracy curve" as a compact inline visual.
 *
 * Scaling is per-series by default (a loss curve has no meaningful absolute
 * range). Pass `domain` to pin it — utilization is a percentage, and letting a
 * box that idles between 0 and 3% draw the same dramatic sawtooth as one
 * swinging 0-100 would make the fleet trend a liar.
 */
export default function Sparkline({ points, height = 46, stroke = 'var(--active)', domain = null }) {
  const ys = (points || []).filter(v => Number.isFinite(v));
  if (ys.length < 2) return null;
  const W = 240;
  const [min, max] = domain || [Math.min(...ys), Math.max(...ys)];
  const span = max - min || 1;
  const dx = W / (ys.length - 1);
  const pad = 4;
  const coords = ys.map((v, i) => {
    const x = i * dx;
    const y = height - pad - ((v - min) / span) * (height - pad * 2);
    return [x, y];
  });
  const d = coords.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const [lx, ly] = coords[coords.length - 1];
  return (
    <svg
      className="msparkline"
      width="100%"
      height={height}
      viewBox={`0 0 ${W} ${height}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path d={d} fill="none" stroke={stroke} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
      <circle cx={lx} cy={ly} r="2.6" fill={stroke} />
    </svg>
  );
}

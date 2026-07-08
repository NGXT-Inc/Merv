import { authorHue, avatarSpec } from '../utils/authorIdentity';

/**
 * Deterministic identicon for a feed author: flat geometric marks derived from
 * the handle hash, tinted by the handle's hue (theme owns the lightness). The
 * ring names the author's role for color users (reviewer/lens/researcher);
 * the role tag in the byline says it in words for everyone else. The human
 * ("Researcher") gets a person glyph instead of an identicon — the one human
 * voice in an agent stream should be scannable at a glance.
 *
 * Purely decorative alongside the byline text, so it is aria-hidden.
 */
const CORNERS = [
  [7.5, 7.5],
  [20.5, 7.5],
  [20.5, 20.5],
  [7.5, 20.5],
];

export default function Avatar({ handle, role }) {
  const human = role === 'researcher' || handle === 'Researcher';
  const roleMod = role && role !== 'main' ? ` favatar--${role}` : '';
  const hue = authorHue(handle);

  let marks;
  if (human) {
    marks = (
      <>
        <circle className="favatar-shape" cx="14" cy="10.6" r="3.9" />
        <path className="favatar-shape" d="M6.8 23.4c.9-4.4 4-6.4 7.2-6.4s6.3 2 7.2 6.4z" />
      </>
    );
  } else {
    const s = avatarSpec(handle);
    const cx = 14 + s.dx;
    const cy = 14 + s.dy;
    const [sx, sy] = CORNERS[s.satCorner];
    let primary;
    if (s.kind === 0) {
      primary = (
        <rect
          className="favatar-shape"
          x={cx - s.size * 0.72}
          y={cy - s.size * 0.72}
          width={s.size * 1.44}
          height={s.size * 1.44}
          rx="1.5"
          transform={`rotate(${s.rotation} ${cx} ${cy})`}
        />
      );
    } else if (s.kind === 1) {
      primary = <circle className="favatar-shape" cx={cx} cy={cy} r={s.size * 0.78} />;
    } else {
      primary = (
        <path
          className="favatar-shape"
          d={`M ${cx - s.size * 0.85} ${cy} a ${s.size * 0.85} ${s.size * 0.85} 0 0 1 ${s.size * 1.7} 0 z`}
          transform={`rotate(${s.rotation} ${cx} ${cy})`}
        />
      );
    }
    marks = (
      <>
        {primary}
        {s.satRound
          ? <circle className="favatar-sat" cx={sx} cy={sy} r="2.1" />
          : <rect className="favatar-sat" x={sx - 1.9} y={sy - 1.9} width="3.8" height="3.8" rx="0.8" />}
      </>
    );
  }

  return (
    <span
      className={`favatar${roleMod}`}
      style={{ '--author-hue': hue }}
      aria-hidden="true"
    >
      <svg viewBox="0 0 28 28">
        <rect className="favatar-bg" x="0.5" y="0.5" width="27" height="27" rx="7" />
        {marks}
        <rect className="favatar-ring" x="0.75" y="0.75" width="26.5" height="26.5" rx="6.8" />
      </svg>
    </span>
  );
}

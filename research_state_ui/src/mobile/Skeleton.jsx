/**
 * Skeleton placeholders — replace the literal "Loading…" text on async mobile
 * screens with shimmer rows that match the eventual layout.
 */
export function Skeleton({ lines = 3 }) {
  return (
    <div className="mskel" aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="mskel-line" style={{ width: `${90 - (i % 3) * 16}%` }} />
      ))}
    </div>
  );
}

export function SkeletonCards({ count = 3 }) {
  return (
    <div className="mcard-list" aria-hidden="true">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="mcard mskel-card">
          <div className="mskel-line" style={{ width: '58%' }} />
          <div className="mskel-line" style={{ width: '92%' }} />
          <div className="mskel-line" style={{ width: '40%' }} />
        </div>
      ))}
    </div>
  );
}

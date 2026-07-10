import { useRef, useState } from 'react';

const KNOB = 44;
const PAD = 4;

/**
 * SlideToConfirm — a deliberate drag gesture for the one sanctioned destructive
 * mobile action (sandbox release). Replaces the tap-tap confirm that muscle
 * memory blows through.
 *
 * Fires onConfirm() once the knob reaches the end; the parent owns the busy
 * state and unmounts/resets on completion.
 */
export default function SlideToConfirm({
  label = 'Slide to release',
  busyLabel = 'Releasing…',
  busy = false,
  onConfirm,
}) {
  const trackRef = useRef(null);
  const [x, setX] = useState(0);
  const [armed, setArmed] = useState(false);
  const dragging = useRef(false);

  const maxX = () => (trackRef.current ? Math.max(0, trackRef.current.clientWidth - KNOB - PAD * 2) : 0);

  const move = (clientX) => {
    if (!dragging.current || armed || busy) return;
    const rect = trackRef.current.getBoundingClientRect();
    const nx = Math.max(0, Math.min(maxX(), clientX - rect.left - KNOB / 2));
    setX(nx);
  };
  const start = () => { if (!busy && !armed) dragging.current = true; };
  const end = () => {
    if (!dragging.current) return;
    dragging.current = false;
    if (x >= maxX() - 6 && !armed) {
      setArmed(true);
      setX(maxX());
      try { navigator.vibrate?.(18); } catch { /* unsupported */ }
      onConfirm?.();
    } else {
      setX(0);
    }
  };

  return (
    <div
      ref={trackRef}
      className={`slide-confirm${armed || busy ? ' is-armed' : ''}`}
      onTouchMove={(e) => move(e.touches[0].clientX)}
      onTouchEnd={end}
      onTouchCancel={end}
      onMouseMove={(e) => move(e.clientX)}
      onMouseUp={end}
      onMouseLeave={end}
    >
      <span className="slide-confirm-label">{busy ? busyLabel : armed ? 'Confirmed' : label}</span>
      <button
        type="button"
        className="slide-confirm-knob"
        aria-label={label}
        style={{ transform: `translateX(${x}px)`, transition: dragging.current ? 'none' : undefined }}
        onTouchStart={start}
        onMouseDown={start}
        disabled={busy || armed}
      >
        →
      </button>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from 'react';

// Sticky app bar + eyebrow clearance when jumping to a section.
const SCROLL_OFFSET = 74;

const prefersReducedMotion = () =>
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// A press that travels less than this is a tap; past it, a scrub.
const TAP_SLOP = 8;

/**
 * The right-edge scrub rail (design_handoff_mobile_redesign, sketches 3a/3b).
 * Experiment-detail ONLY — a pure overlay that never reshapes content.
 *
 * At rest: a ~15px translucent strip of faint ticks, the current section's
 * tick orange; it doubles as a scroll-position readout. The first touch only
 * PEEKS — the panel opens, nothing scrolls, and it stays open (subconscious
 * frictionlessness: look before you commit). From there, tapping a label
 * jumps to that section and dismisses; dragging past the slop scrubs
 * directly and dismisses on release; tapping the dim dismisses.
 */
export default function ScrubRail({ sections }) {
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const railRef = useRef(null);
  const activeRef = useRef(0);
  const openRef = useRef(false);
  const gestureRef = useRef(null); // { startY, wasOpen, dragging } while a pointer is down
  activeRef.current = active;
  openRef.current = open;

  const scrollToSection = useCallback((i, { instant = false } = {}) => {
    const el = sections[i]?.ref.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY - SCROLL_OFFSET;
    window.scrollTo({ top, behavior: instant || prefersReducedMotion() ? 'instant' : 'smooth' });
  }, [sections]);

  // Passive readout: which section owns the current scroll position.
  useEffect(() => {
    let raf = 0;
    const onScroll = () => {
      if (gestureRef.current?.dragging) return;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const probe = window.scrollY + SCROLL_OFFSET + window.innerHeight / 4;
        let current = 0;
        sections.forEach((s, i) => {
          const el = s.ref.current;
          if (el && el.getBoundingClientRect().top + window.scrollY <= probe) current = i;
        });
        if (current !== activeRef.current) setActive(current);
      });
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => { window.removeEventListener('scroll', onScroll); cancelAnimationFrame(raf); };
  }, [sections]);

  const indexAtY = useCallback((clientY) => {
    const rect = railRef.current.getBoundingClientRect();
    const frac = (clientY - rect.top) / rect.height;
    return Math.max(0, Math.min(sections.length - 1, Math.floor(frac * sections.length)));
  }, [sections.length]);

  // The opening touch moves nothing: peek first, choose second.
  const onPointerDown = (e) => {
    gestureRef.current = { startY: e.clientY, wasOpen: openRef.current, dragging: false };
    try { railRef.current.setPointerCapture(e.pointerId); } catch { /* capture is best-effort */ }
    if (!openRef.current) setOpen(true);
  };
  const onPointerMove = (e) => {
    const g = gestureRef.current;
    if (!g) return;
    if (!g.dragging && Math.abs(e.clientY - g.startY) > TAP_SLOP) g.dragging = true;
    if (!g.dragging) return;
    const i = indexAtY(e.clientY);
    if (i !== activeRef.current) {
      setActive(i);
      scrollToSection(i, { instant: true }); // scrubbing must feel direct
    }
  };
  const onPointerUp = (e) => {
    const g = gestureRef.current;
    gestureRef.current = null;
    if (!g) return;
    if (g.dragging) { setOpen(false); return; } // scrub done — release dismisses
    if (g.wasOpen) {
      // Tap on the open panel: jump to the chosen section, then dismiss.
      const i = indexAtY(e.clientY);
      setActive(i);
      scrollToSection(i);
      setOpen(false);
    }
    // Otherwise this tap just opened the panel — it stays for the next tap.
  };
  const onPointerCancel = () => {
    gestureRef.current = null;
    setOpen(false);
  };

  return (
    <>
    {open && <div className="mrail-dim" onPointerDown={() => setOpen(false)} aria-hidden="true" />}
    <div
      ref={railRef}
      className={`mrail${open ? ' mrail--open' : ''}`}
      role="slider"
      aria-label="Section scrubber"
      aria-valuemin={0}
      aria-valuemax={sections.length - 1}
      aria-valuenow={active}
      aria-valuetext={sections[active]?.label}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
    >
      {sections.map((s, i) => (
        open ? (
          <span key={s.id} className={`mrail-lab${i === active ? ' on' : ''}`}>
            <span>{s.label}</span>
            <span className="mrail-dot" aria-hidden="true" />
          </span>
        ) : (
          <span key={s.id} className={`mrail-tick${i === active ? ' on' : ''}`} aria-hidden="true" />
        )
      ))}
    </div>
    </>
  );
}

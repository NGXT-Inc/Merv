import { useCallback, useEffect, useRef, useState } from 'react';

// Sticky app bar + eyebrow clearance when jumping to a section.
const SCROLL_OFFSET = 74;

const prefersReducedMotion = () =>
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * The right-edge scrub rail (design_handoff_mobile_redesign, sketches 3a/3b).
 * Experiment-detail ONLY — a pure overlay that never reshapes content.
 *
 * At rest: a ~15px translucent strip of faint ticks, the current section's
 * tick orange. On touch it widens into a frosted panel with the section
 * labels; dragging scrubs between sections, releasing snaps the rail shut.
 * It doubles as a scroll-position readout: the active tick tracks scroll.
 */
export default function ScrubRail({ sections }) {
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const railRef = useRef(null);
  const activeRef = useRef(0);
  const draggingRef = useRef(false);
  activeRef.current = active;

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
      if (draggingRef.current) return;
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

  const onPointerDown = (e) => {
    draggingRef.current = true;
    setOpen(true);
    try { railRef.current.setPointerCapture(e.pointerId); } catch { /* capture is best-effort */ }
    const i = indexAtY(e.clientY);
    setActive(i);
    scrollToSection(i, { instant: true });
  };
  const onPointerMove = (e) => {
    if (!draggingRef.current) return;
    const i = indexAtY(e.clientY);
    if (i !== activeRef.current) {
      setActive(i);
      scrollToSection(i, { instant: true }); // scrubbing must feel direct
    }
  };
  const endDrag = () => {
    draggingRef.current = false;
    setOpen(false);
  };

  return (
    <>
    {open && <div className="mrail-dim" aria-hidden="true" />}
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
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
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

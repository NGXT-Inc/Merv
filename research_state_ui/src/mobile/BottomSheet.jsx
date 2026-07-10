import { useEffect, useRef, useState } from 'react';
import { useScrollLock } from './useScrollLock';

/**
 * BottomSheet — the mobile overlay primitive.
 *
 * A scrim + rounded panel anchored to the bottom of the viewport, with a drag
 * handle that dismisses on a downward drag/fling. Backs the More sheet and
 * every detail/picker overlay (graph node detail, sort menus, …) so the iOS
 * scroll-lock fix lives in exactly one place.
 *
 * Props:
 *   open      — visibility
 *   onClose   — backdrop tap / Esc / drag-dismiss
 *   label     — aria-label for the dialog
 *   title     — optional sticky header row
 *   children  — sheet body (scrolls)
 *   footer    — optional pinned footer (rendered in .msheet-foot)
 */
export default function BottomSheet({ open, onClose, label, title = null, children, footer = null }) {
  const [dragY, setDragY] = useState(0);
  const startRef = useRef(null);
  useScrollLock(open);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => { if (open) setDragY(0); }, [open]);

  if (!open) return null;

  const onTouchStart = (e) => { startRef.current = e.touches[0].clientY; };
  const onTouchMove = (e) => {
    if (startRef.current == null) return;
    const dy = e.touches[0].clientY - startRef.current;
    setDragY(dy > 0 ? dy : 0);
  };
  const onTouchEnd = () => {
    if (dragY > 90) onClose?.();
    setDragY(0);
    startRef.current = null;
  };

  return (
    <>
      <div className="msheet-backdrop" onClick={onClose} />
      <div
        className="msheet"
        role="dialog"
        aria-modal="true"
        aria-label={label}
        style={dragY ? { transform: `translateY(${dragY}px)`, transition: 'none' } : undefined}
      >
        <div
          className="msheet-grip-zone"
          onTouchStart={onTouchStart}
          onTouchMove={onTouchMove}
          onTouchEnd={onTouchEnd}
        >
          <div className="msheet-grip" aria-hidden="true" />
        </div>
        {title && <div className="msheet-title">{title}</div>}
        {children}
        {footer && <div className="msheet-foot">{footer}</div>}
      </div>
    </>
  );
}

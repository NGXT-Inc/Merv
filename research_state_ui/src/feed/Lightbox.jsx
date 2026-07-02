import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

/**
 * Tap-to-zoom viewer for feed media. Deliberately minimal: scrim + image +
 * close. Esc, scrim-click, or the button dismiss it; body scroll is locked
 * and focus is held inside while it is open (the trigger restores its own
 * focus via onClose).
 */
export default function Lightbox({ src, alt = '', onClose }) {
  const closeRef = useRef(null);
  // Keyboard handler reads the latest onClose through a ref, so the listener
  // attaches once instead of re-binding on every parent render.
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onCloseRef.current();
      // The close button is the dialog's only tab stop — hold focus on it.
      if (e.key === 'Tab') {
        e.preventDefault();
        closeRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    closeRef.current?.focus();
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, []);

  return createPortal(
    <div
      className="feed-lightbox"
      role="dialog"
      aria-modal="true"
      aria-label="Image viewer"
      onClick={onClose}
    >
      <img
        src={src}
        alt={alt}
        className="feed-lightbox-img"
        onClick={(e) => e.stopPropagation()}
      />
      <button
        ref={closeRef}
        type="button"
        className="feed-lightbox-close"
        aria-label="Close image viewer"
        onClick={onClose}
      >
        ✕
      </button>
    </div>,
    document.body
  );
}

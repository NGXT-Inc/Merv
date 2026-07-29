import { useEffect, useRef } from 'react';

/**
 * Mount expensive feed media near the viewport on desktop. Mobile remains
 * tap-to-open and only unmounts media after the opened card scrolls away.
 */
export function useMountedMedia({
  isMobile,
  state,
  rootRef,
  open,
  close,
}) {
  const actionsRef = useRef({ open, close });
  actionsRef.current = { open, close };

  useEffect(() => {
    if (isMobile || !rootRef.current) return undefined;
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && state === 'poster') {
          actionsRef.current.open();
        } else if (!entry.isIntersecting && state === 'open') {
          actionsRef.current.close();
        }
      }
    }, { rootMargin: '200% 0px 200% 0px' });
    observer.observe(rootRef.current);
    return () => observer.disconnect();
  }, [isMobile, state, rootRef]);

  useEffect(() => {
    if (!isMobile || state !== 'open' || !rootRef.current) return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => !entry.isIntersecting)) {
        actionsRef.current.close();
      }
    }, { rootMargin: '150% 0px 150% 0px' });
    observer.observe(rootRef.current);
    return () => observer.disconnect();
  }, [isMobile, state, rootRef]);
}

import { useEffect } from 'react';

/**
 * Lock body scroll while `active`, preserving and restoring the scroll
 * position. Uses the position:fixed body technique so iOS Safari does not
 * rubber-band the page *behind* an overlay (the scroll-leak the mobile plan
 * calls out). See docs/MOBILE_UX_REVIEW.md §3.2.
 */
export function useScrollLock(active) {
  useEffect(() => {
    if (!active) return undefined;
    const scrollY = window.scrollY;
    const body = document.body;
    const prev = {
      position: body.style.position,
      top: body.style.top,
      width: body.style.width,
      overflow: body.style.overflow,
    };
    body.style.position = 'fixed';
    body.style.top = `-${scrollY}px`;
    body.style.width = '100%';
    body.style.overflow = 'hidden';
    return () => {
      body.style.position = prev.position;
      body.style.top = prev.top;
      body.style.width = prev.width;
      body.style.overflow = prev.overflow;
      window.scrollTo(0, scrollY);
    };
  }, [active]);
}

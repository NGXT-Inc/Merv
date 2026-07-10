import { useEffect } from 'react';

/**
 * Reference-counted body scroll lock. Uses the position:fixed technique so iOS
 * Safari doesn't rubber-band the page *behind* an overlay. Counting makes
 * nesting safe — a node-detail sheet
 * opened from inside another sheet won't prematurely restore scroll.
 */
let lockCount = 0;
let savedScrollY = 0;
let savedStyles = null;

function lock() {
  if (lockCount === 0) {
    savedScrollY = window.scrollY;
    const body = document.body;
    savedStyles = {
      position: body.style.position,
      top: body.style.top,
      width: body.style.width,
      overflow: body.style.overflow,
    };
    body.style.position = 'fixed';
    body.style.top = `-${savedScrollY}px`;
    body.style.width = '100%';
    body.style.overflow = 'hidden';
  }
  lockCount++;
}

function unlock() {
  lockCount = Math.max(0, lockCount - 1);
  if (lockCount === 0 && savedStyles) {
    const body = document.body;
    body.style.position = savedStyles.position;
    body.style.top = savedStyles.top;
    body.style.width = savedStyles.width;
    body.style.overflow = savedStyles.overflow;
    savedStyles = null;
    window.scrollTo(0, savedScrollY);
  }
}

export function useScrollLock(active) {
  useEffect(() => {
    if (!active) return undefined;
    lock();
    return unlock;
  }, [active]);
}

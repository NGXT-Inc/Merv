import { useEffect, useRef, useState } from 'react';

const THRESHOLD = 70; // px of pull needed to trigger a refresh
const MAX = 110;      // clamp on indicator travel

/**
 * Pull-to-refresh for the document scroller. Returns { distance, refreshing }
 * for a fixed indicator the shell renders. Engages only at scrollTop 0 and
 * only for touch, and resists past the threshold so it feels rubber-bandy.
 * This is the instant override that makes adaptive polling acceptable.
 *
 * Handlers read/write refs (not state) so the window listeners are attached
 * once and never thrash on every touch frame.
 */
export function usePullToRefresh(onRefresh) {
  const [distance, setDistance] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const startY = useRef(null);
  const armed = useRef(false);
  const distRef = useRef(0);
  const refreshingRef = useRef(false);

  const setDist = (d) => { distRef.current = d; setDistance(d); };

  useEffect(() => {
    const onStart = (e) => {
      if (window.scrollY > 0 || refreshingRef.current) { armed.current = false; return; }
      startY.current = e.touches[0].clientY;
      armed.current = true;
    };
    const onMove = (e) => {
      if (!armed.current || startY.current == null) return;
      const dy = e.touches[0].clientY - startY.current;
      if (dy <= 0 || window.scrollY > 0) { setDist(0); return; }
      const d = dy < THRESHOLD ? dy : THRESHOLD + (dy - THRESHOLD) * 0.35;
      setDist(Math.min(MAX, d));
    };
    const onEnd = async () => {
      if (!armed.current) return;
      armed.current = false;
      startY.current = null;
      if (distRef.current >= THRESHOLD && !refreshingRef.current) {
        refreshingRef.current = true;
        setRefreshing(true);
        setDist(THRESHOLD);
        try { await onRefresh?.(); }
        finally {
          refreshingRef.current = false;
          setRefreshing(false);
          setDist(0);
        }
      } else {
        setDist(0);
      }
    };
    window.addEventListener('touchstart', onStart, { passive: true });
    window.addEventListener('touchmove', onMove, { passive: true });
    window.addEventListener('touchend', onEnd);
    window.addEventListener('touchcancel', onEnd);
    return () => {
      window.removeEventListener('touchstart', onStart);
      window.removeEventListener('touchmove', onMove);
      window.removeEventListener('touchend', onEnd);
      window.removeEventListener('touchcancel', onEnd);
    };
  }, [onRefresh]);

  return { distance, refreshing };
}

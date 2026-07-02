/**
 * Backdrop visibility. Default on; storing 'off' under 'rsui:backdrop'
 * disables the ambient canvas entirely (the component unmounts, so a
 * disabled backdrop costs nothing). Same external-store idiom as useTheme.
 */
import { useCallback, useSyncExternalStore } from 'react';

const KEY = 'rsui:backdrop';

let on = (() => {
  try { return localStorage.getItem(KEY) !== 'off'; } catch { return true; }
})();
const listeners = new Set();

export function setBackdrop(next) {
  on = !!next;
  try {
    if (on) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, 'off');
  } catch {
    /* persistence is best-effort */
  }
  for (const fn of listeners) fn();
}

export function useBackdrop() {
  const subscribe = useCallback((fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }, []);
  return useSyncExternalStore(subscribe, () => on);
}

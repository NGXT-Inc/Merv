import { useSyncExternalStore } from 'react';

/**
 * Tiny toast bus — module singleton + useSyncExternalStore, the same pattern
 * as useViewport/useTheme. `toast(msg)` from anywhere; <ToastHost/> (mounted
 * in MobileShell) renders the stack.
 */
let toasts = [];
const listeners = new Set();
let seq = 0;

function emit() { listeners.forEach(l => l()); }

export function toast(message, { variant = 'info', duration = 2600 } = {}) {
  const id = ++seq;
  toasts = [...toasts, { id, message, variant }];
  emit();
  setTimeout(() => {
    toasts = toasts.filter(t => t.id !== id);
    emit();
  }, duration);
  return id;
}

function subscribe(l) { listeners.add(l); return () => listeners.delete(l); }

export function useToasts() {
  return useSyncExternalStore(subscribe, () => toasts, () => toasts);
}

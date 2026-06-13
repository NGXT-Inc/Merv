import { useToasts } from './toastStore';

/**
 * ToastHost — renders the active toast stack just above the bottom nav.
 * Mounted once in MobileShell.
 */
export default function ToastHost() {
  const toasts = useToasts();
  if (!toasts.length) return null;
  return (
    <div className="mtoast-host" role="status" aria-live="polite">
      {toasts.map(t => (
        <div key={t.id} className={`mtoast mtoast--${t.variant}`}>{t.message}</div>
      ))}
    </div>
  );
}

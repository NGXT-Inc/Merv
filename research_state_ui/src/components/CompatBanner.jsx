import { useProjectStore } from '../store/useProjectStore';

/**
 * CompatBanner — surfaces the version/auth handshake result (GET /api/meta and
 * the typed 401/426 codes from api.js). Dormant in local mode: versions match
 * and there is no auth gate, so `compat` stays null and this renders nothing.
 *
 * Against a hosted control plane it announces a version skew (info), an
 * upgrade-required gate (error, 426), or a missing-credential gate (error,
 * 401) — with a Reload affordance where that helps.
 */
export default function CompatBanner() {
  const compat = useProjectStore(s => s.compat);
  const dismissCompat = useProjectStore(s => s.dismissCompat);
  if (!compat) return null;

  const level = compat.level || 'info';
  return (
    <div className={`compat-banner compat-banner--${level}`} role="status">
      <span>{compat.message}</span>
      <span className="compat-banner-spacer" />
      {compat.action === 'reload' && (
        <button type="button" className="compat-banner-btn" onClick={() => window.location.reload()}>
          Reload
        </button>
      )}
      <button
        type="button"
        className="compat-banner-dismiss"
        aria-label="Dismiss"
        onClick={dismissCompat}
      >
        ×
      </button>
    </div>
  );
}

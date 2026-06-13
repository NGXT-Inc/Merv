import { useEffect, useState } from 'react';
import { NavLink, Link, useLocation } from 'react-router-dom';
import { useProjectStore, selectStats, selectSandboxes, selectActiveExperiments, selectReviewRequests } from '../store/useProjectStore';
import { useTheme } from '../store/useTheme';
import ProjectSwitcher from '../components/ProjectSwitcher';
import { setSurfaceOverride } from '../store/useViewport';

const NEXT_THEME_MODE = { light: 'dark', dark: 'system', system: 'light' };

// Statuses where an experiment sits at a review gate — the supervisor's
// main "needs attention" signal for the Now tab badge.
const REVIEW_STATES = new Set(['design_review', 'experiment_review']);

function fmtSyncedAgo(ms, now) {
  if (!ms) return 'never';
  const s = Math.max(0, Math.floor((now - ms) / 1000));
  if (s < 5) return 'now';
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h`;
}

/**
 * Mobile app shell: top bar (project · freshness · theme), routed content,
 * 4-tab bottom nav, and a More sheet hosting everything that lives in the
 * desktop sidebar. While mounted it tags <html data-surface="mobile"> so
 * mobile.css applies — desktop styling is untouched by construction.
 */
export default function MobileShell({ children, onRefresh }) {
  const location = useLocation();
  const home = useProjectStore(s => s.home);
  const lastSyncedAt = useProjectStore(s => s.lastSyncedAt);
  const lastSyncError = useProjectStore(s => s.lastSyncError);
  const isPolling = useProjectStore(s => s.isPolling);
  const activeExperiments = useProjectStore(selectActiveExperiments);
  const reviewRequests = useProjectStore(selectReviewRequests);
  const [sheetOpen, setSheetOpen] = useState(false);
  // 10s tick so the "synced Xs" label and staleness stay honest even when
  // polling has stopped delivering new store state (unreachable daemon).
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 10000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.surface = 'mobile';
    return () => { delete document.documentElement.dataset.surface; };
  }, []);

  // Any navigation closes the sheet.
  useEffect(() => { setSheetOpen(false); }, [location.pathname]);

  const projectName = home?.project?.name || 'Research State';
  const stale = lastSyncError || (lastSyncedAt && now - lastSyncedAt > 30000);
  const dotClass = stale ? 'sync-dot stale' : (isPolling ? 'sync-dot' : 'sync-dot paused');

  const attentionCount =
    activeExperiments.filter(e => REVIEW_STATES.has(e.status)).length +
    reviewRequests.filter(r => r.status === 'requested' || r.status === 'started').length;

  return (
    <div className="mshell">
      <header className="mbar">
        <div className="mbar-title">{projectName}</div>
        <div className="mbar-sync" aria-label={stale ? 'data stale' : 'data live'}>
          <span className={dotClass} />
          {fmtSyncedAgo(lastSyncedAt, now)}
        </div>
        <ThemeButton />
      </header>

      <main className="mshell-main">{children}</main>

      <nav className="mnav" aria-label="Primary">
        <NavLink to="/" end className={({ isActive }) => 'mnav-tab' + (isActive ? ' active' : '')}>
          <span className="mnav-glyph" aria-hidden="true">◉</span>
          Now
          {attentionCount > 0 && <span className="mnav-badge">{attentionCount}</span>}
        </NavLink>
        <NavLink to="/experiments" className={({ isActive }) => 'mnav-tab' + (isActive ? ' active' : '')}>
          <span className="mnav-glyph" aria-hidden="true">⚗</span>
          Experiments
        </NavLink>
        <NavLink to="/events" className={({ isActive }) => 'mnav-tab' + (isActive ? ' active' : '')}>
          <span className="mnav-glyph" aria-hidden="true">≋</span>
          Activity
        </NavLink>
        <button
          type="button"
          className={'mnav-tab' + (sheetOpen ? ' active' : '')}
          onClick={() => setSheetOpen(v => !v)}
          aria-expanded={sheetOpen}
        >
          <span className="mnav-glyph" aria-hidden="true">⋯</span>
          More
        </button>
      </nav>

      {sheetOpen && <MoreSheet onClose={() => setSheetOpen(false)} onRefresh={onRefresh} />}
    </div>
  );
}

function ThemeButton() {
  const { mode, theme, setMode } = useTheme();
  return (
    <button
      type="button"
      className="mbar-btn"
      onClick={() => setMode(NEXT_THEME_MODE[mode])}
      aria-label={`Theme: ${mode}. Tap to switch.`}
    >
      <span aria-hidden="true">{theme === 'dark' ? '◑' : '◐'}</span>
    </button>
  );
}

function MoreSheet({ onClose, onRefresh }) {
  const stats = useProjectStore(selectStats);
  const home = useProjectStore(s => s.home);
  const lastSyncError = useProjectStore(s => s.lastSyncError);
  const sandboxes = useProjectStore(selectSandboxes);
  const runningSandboxes = sandboxes.filter(s => s.status === 'running').length;

  return (
    <>
      <div className="msheet-backdrop" onClick={onClose} />
      <div className="msheet" role="dialog" aria-label="More">
        <div className="msheet-grip" aria-hidden="true" />

        <ProjectSwitcher />

        <div className="msheet-section">Browse</div>
        <SheetLink to="/claims" label="Claims" count={stats.claims ?? home?.claims?.length ?? 0} />
        <SheetLink to="/reviews" label="Reviews" count={stats.open_reviews ?? stats.reviews ?? 0} />
        <SheetLink to="/resources" label="Resources" count={stats.resources ?? 0} />
        <SheetLink to="/sandboxes" label="Sandboxes" count={runningSandboxes ? `${runningSandboxes} running` : null} />
        <SheetLink to="/projects" label="Projects" />

        <div className="msheet-section">Forensics</div>
        <SheetLink to="/activity" label="Live traffic" />
        <SheetLink to="/debug" label="Tool I/O" note="desktop recommended" />
        <SheetLink to="/visual/dag" label="Logic DAG" note="desktop recommended" />

        <div className="msheet-foot">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => { onRefresh?.(); onClose(); }}>
            Refresh now
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setSurfaceOverride('desktop')}>
            Use desktop layout
          </button>
        </div>
        {lastSyncError && (
          <div className="error-message" style={{ marginTop: 10, fontSize: 11 }}>{lastSyncError}</div>
        )}
      </div>
    </>
  );
}

function SheetLink({ to, label, count = null, note = null }) {
  return (
    <Link to={to} className="msheet-link">
      <span>{label}</span>
      <span className="msheet-count">
        {note && <span className="msheet-link-note">{note} </span>}
        {count != null && count !== 0 ? count : ''}
      </span>
    </Link>
  );
}

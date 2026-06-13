import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useProjectStore, selectExperiments, selectEventsAll } from '../store/useProjectStore';
import { api } from '../api';
import ObjId from '../components/ObjId';
import SandboxTerminal from '../components/SandboxTerminal';
import { expName } from '../utils/experiment';

const STATUS_TABS = ['all', 'running', 'provisioning', 'terminated'];

// Parachute = results rescued to cloud object storage when a sandbox expired
// while the local daemon was offline. Map each lifecycle event to a chip.
const PARACHUTE_CHIPS = {
  'sandbox.parachuted':         { variant: 'parachuted', label: 'Parachuted' },
  'sandbox.parachute_restored': { variant: 'restored',   label: 'Restored' },
  'sandbox.parachute_failed':   { variant: 'failed',     label: '⚠ Parachute failed' },
};

// Newest parachute-lifecycle event type for an experiment/sandbox, or null.
// Scans the deep events window and matches on either the experiment target or
// the sandbox_id in the payload; picks the latest by id/created_at so ordering
// of the feed doesn't matter.
function latestParachute(events, experimentId, sandboxId) {
  let best = null;
  for (const ev of events) {
    const type = ev.event_type || ev.type;
    if (!PARACHUTE_CHIPS[type]) continue;
    if (ev.target_id !== experimentId && ev.payload?.sandbox_id !== sandboxId) continue;
    if (!best || String(ev.id ?? ev.created_at ?? '') > String(best.id ?? best.created_at ?? '')) {
      best = ev;
    }
  }
  return best ? (best.event_type || best.type) : null;
}

// Column template (chevron · status · experiment · hardware · uptime · expires
// · endpoint · links) lives in CSS as --sbxt-cols so the head and every row
// share one source of truth and stay aligned.

/**
 * Sandboxes index — the compute fleet as an infra table.
 *
 * One sandbox per experiment; the agent procures them over MCP and drives them
 * over SSH. This page is the instance console: status, hardware, lifetime, and
 * endpoint per row, with an expand-to-terminal drawer (the live terminal UI is
 * unchanged — see SandboxTerminal).
 */
export default function Sandboxes() {
  const projectId = useProjectStore(s => s.projectId);
  const experiments = useProjectStore(selectExperiments);
  const events = useProjectStore(selectEventsAll);
  const [sandboxes, setSandboxes] = useState(null);
  const [error, setError] = useState(null);
  const [filterStatus, setFilterStatus] = useState('all');
  const [expanded, setExpanded] = useState(null);
  const [now, setNow] = useState(Date.now());

  const fetchSandboxes = useCallback(async () => {
    try {
      const data = await api.listSandboxes(projectId);
      setSandboxes(data.sandboxes || []);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, [projectId]);

  useEffect(() => { fetchSandboxes(); }, [fetchSandboxes]);

  useEffect(() => {
    const anyActive = (sandboxes || []).some(
      s => s.status === 'running' || s.status === 'provisioning',
    );
    const t = setInterval(fetchSandboxes, anyActive ? 3000 : 10000);
    return () => clearInterval(t);
  }, [fetchSandboxes, sandboxes]);

  // 1Hz tick for live uptime / "expires in" labels.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const expById = useMemo(() => Object.fromEntries(experiments.map(e => [e.id, e])), [experiments]);

  const counts = useMemo(() => {
    const map = { all: (sandboxes || []).length };
    for (const s of (sandboxes || [])) map[s.status] = (map[s.status] || 0) + 1;
    return map;
  }, [sandboxes]);

  const filtered = useMemo(() => {
    let list = sandboxes || [];
    if (filterStatus !== 'all') list = list.filter(s => s.status === filterStatus);
    const rank = (st) => (st === 'running' ? 0 : st === 'provisioning' ? 1 : 2);
    return list.slice().sort((a, b) => {
      const ar = rank(a.status);
      const br = rank(b.status);
      if (ar !== br) return ar - br;
      return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
    });
  }, [sandboxes, filterStatus]);

  return (
    <div className="page-stage">
      <header className="page-header">
        <div className="page-eyebrow">Sandboxes</div>
        <h1 className="page-title">Compute fleet</h1>
        <div className="tab-row" style={{ marginTop: 12 }}>
          {STATUS_TABS.map(s => (
            <button key={s} className={`tab${filterStatus === s ? ' active' : ''}`} onClick={() => setFilterStatus(s)}>
              {s === 'running' && (counts.running || 0) > 0 && <span className="sbxt-tab-dot" />}
              {s}
              <span className="tab-count">{counts[s] || 0}</span>
            </button>
          ))}
        </div>
      </header>

      {error && <div className="error-message">{error}</div>}

      {sandboxes == null ? (
        <div className="empty">Loading…</div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <h2>No sandboxes</h2>
          <p>
            {sandboxes.length === 0
              ? 'The agent provisions one with sandbox.request once an experiment is ready_to_run.'
              : `No ${filterStatus} sandboxes.`}
          </p>
        </div>
      ) : (
        <div className="sbxt-scroll">
          <div className="sbxt">
            <div className="sbxt-head">
              <span aria-hidden="true" />
              <span className="sbxt-th">Status</span>
              <span className="sbxt-th">Experiment</span>
              <span className="sbxt-th">Hardware</span>
              <span className="sbxt-th sbxt-th--r">Uptime</span>
              <span className="sbxt-th sbxt-th--r">Expires</span>
              <span className="sbxt-th">SSH endpoint</span>
              <span className="sbxt-th sbxt-th--r">Links</span>
            </div>
            {filtered.map(s => (
              <SandboxRow
                key={s.experiment_id}
                sandbox={s}
                experiment={expById[s.experiment_id]}
                projectId={projectId}
                now={now}
                parachute={latestParachute(events, s.experiment_id, s.sandbox_id)}
                open={expanded === s.experiment_id}
                onToggle={() => setExpanded(expanded === s.experiment_id ? null : s.experiment_id)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function SandboxRow({ sandbox, experiment, projectId, now, parachute, open, onToggle }) {
  const s = sandbox;
  const live = s.status === 'running';
  const chip = parachute ? PARACHUTE_CHIPS[parachute] : null;
  const title = experiment ? expName(experiment) : s.experiment_id;
  const hardware = [
    s.gpu,
    s.cpu && `${s.cpu} cpu`,
    s.memory && `${Math.round(s.memory / 1024)} GiB`,
  ].filter(Boolean).join(' · ');
  const endpoint = s.ssh_host && s.ssh_port ? `${s.ssh_user || 'root'}@${s.ssh_host}:${s.ssh_port}` : null;

  const expiresMs = live && s.expires_at ? Date.parse(s.expires_at) - now : null;
  const expiresCls = expiresMs == null ? '' : expiresMs < 120000 ? ' sbxt-warn--hot' : expiresMs < 600000 ? ' sbxt-warn' : '';

  const onKey = (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); }
  };

  return (
    <div className={`sbxt-rowgroup${open ? ' open' : ''}`}>
      <div
        className="sbxt-row"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={onToggle}
        onKeyDown={onKey}
      >
        <span className={`sbxt-twist${open ? ' open' : ''}`} aria-hidden="true">▸</span>
        <span className="sbxt-status">
          <span className={`sbxt-dot sbxt-dot--${s.status}`} />
          <span className="sbxt-status-label">{s.status}</span>
          {chip && <span className={`parachute-chip parachute-chip--${chip.variant}`}>{chip.label}</span>}
        </span>
        <span className="sbxt-exp">
          <span className="sbxt-exp-title">{title}</span>
          <span className="sbxt-exp-id"><ObjId id={s.experiment_id} /></span>
        </span>
        <span className="sbxt-hw mono" title={hardware}>{hardware || '—'}</span>
        <span className="sbxt-num">{live && s.requested_at ? fmtDur(now - Date.parse(s.requested_at)) : '—'}</span>
        <span className={`sbxt-num${expiresCls}`}>
          {expiresMs == null ? '—' : expiresMs <= 0 ? 'soon' : fmtDur(expiresMs)}
        </span>
        <span className="sbxt-ep mono" title={endpoint || ''}>{endpoint || '—'}</span>
        <span className="sbxt-links" onClick={(e) => e.stopPropagation()}>
          <Link to={`/experiments/${s.experiment_id}#execution`} className="sbxt-link">open ↗</Link>
          <DashboardChips dashboards={s.dashboards} />
        </span>
      </div>
      {open && (
        <div className="sbxt-drawer">
          <SandboxTerminal projectId={projectId} experimentId={s.experiment_id} />
        </div>
      )}
    </div>
  );
}

function DashboardChips({ dashboards }) {
  if (!dashboards) return null;
  const entries = [
    dashboards.mlflow && { key: 'mlflow', label: 'MLflow', url: dashboards.mlflow },
    dashboards.tensorboard && { key: 'tensorboard', label: 'TB', url: dashboards.tensorboard },
  ].filter(Boolean);
  if (entries.length === 0) return null;
  return (
    <>
      {entries.map((e) => (
        <a
          key={e.key}
          href={e.url}
          target="_blank"
          rel="noreferrer noopener"
          className="sbxt-link sbxt-link--muted"
          title={`Open ${e.label} for this sandbox in a new tab`}
        >
          {e.label} ↗
        </a>
      ))}
    </>
  );
}

function fmtDur(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h${m % 60 ? ` ${m % 60}m` : ''}`;
}

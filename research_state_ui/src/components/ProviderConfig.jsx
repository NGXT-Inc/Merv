import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import ProviderIcon from './ProviderIcon';
import ProviderSetupModal from './ProviderSetupModal';

/**
 * ProviderConfig — the Configure page of the compute fleet.
 *
 * One card per connectable cloud (Modal is managed, not listed). Setup runs
 * in the guided wizard modal; a card only grows the agent-facing enable
 * switch once its connection is set up (own credentials, platform
 * credentials, or deployment environment). Secrets are write-only end to
 * end. The daily spend cap renders on connected cards and is enforced by
 * quota admission on the backend.
 */

function statusOf(p) {
  if (!p.setup_complete) return { label: 'not set up', cls: 'off' };
  if (p.credential_source === 'platform') return { label: p.platform_label || 'platform credentials', cls: 'env' };
  if (p.credential_source === 'saved') {
    return p.verified_at
      ? { label: 'connected · verified', cls: 'ok' }
      : { label: 'connected', cls: 'ok' };
  }
  return { label: 'via environment', cls: 'env' };
}

function Switch({ checked, disabled, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`sbxp-switch${checked ? ' on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span className="sbxp-switch-knob" />
    </button>
  );
}

function DailyLimit({ projectId, provider, onUpdated }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const p = provider;

  const save = async () => {
    setBusy(true);
    try {
      const trimmed = draft.trim();
      onUpdated(await api.setSandboxProviderDailyLimit(
        projectId, p.provider, trimmed === '' ? null : Number(trimmed),
      ));
      setEditing(false);
    } finally {
      setBusy(false);
    }
  };

  if (!editing) {
    return (
      <p className="sbxp-limit-row">
        Daily cap: {p.daily_usd_limit == null ? 'none' : `$${p.daily_usd_limit}/day`}
        <button
          type="button"
          className="sbxp-expand"
          onClick={() => {
            setDraft(p.daily_usd_limit == null ? '' : String(p.daily_usd_limit));
            setEditing(true);
          }}
        >
          edit
        </button>
      </p>
    );
  }
  return (
    <p className="sbxp-limit-row">
      <input
        type="number"
        min="0"
        step="1"
        className="sbxp-limit-input"
        value={draft}
        placeholder="USD/day"
        autoFocus
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false); }}
      />
      <button type="button" className="sbxp-expand" disabled={busy} onClick={save}>save</button>
      <button type="button" className="sbxp-expand" onClick={() => setEditing(false)}>cancel</button>
    </p>
  );
}

function ProviderCard({ projectId, provider, onUpdated, onSetup }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const p = provider;
  const status = statusOf(p);
  const usable = p.setup_complete && p.enabled;

  const toggle = async (enabled) => {
    setBusy(true); setError(null);
    try {
      onUpdated(await api.setSandboxProviderEnabled(projectId, p.provider, enabled));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`sbxp-card${usable ? ' sbxp-card--usable' : ''}`}>
      <div className="sbxp-head">
        <div className="sbxp-title">
          <ProviderIcon provider={p.provider} />
          <span className="sbxp-name">{p.label}</span>
        </div>
        {p.setup_complete && (
          <Switch
            checked={p.enabled}
            disabled={busy}
            onChange={toggle}
            label={`${p.enabled ? 'Disable' : 'Enable'} ${p.label} for agents`}
          />
        )}
      </div>
      <div className="sbxp-chiprow">
        <span className={`sbxp-chip sbxp-chip--${status.cls}`}>{status.label}</span>
        {p.fleet_default && <span className="sbxp-chip sbxp-chip--default">default</span>}
      </div>
      <p className="sbxp-note">
        {p.note}{' '}
        <a href={p.console_url} target="_blank" rel="noreferrer">console ↗</a>
      </p>
      {p.setup_complete && !p.enabled && (
        <p className="sbxp-state sbxp-state--off">Agents will not procure compute here.</p>
      )}
      {p.setup_complete && p.enabled && p.connected && !p.in_env_fleet && (
        <p className="sbxp-state">
          Add <code>{p.provider}</code> to MERV_EXECUTION_BACKENDS to put it in
          the active fleet.
        </p>
      )}
      <div className="sbxp-foot">
        {p.setup_complete ? (
          <>
            <DailyLimit projectId={projectId} provider={p} onUpdated={onUpdated} />
            <button type="button" className="sbxp-expand" onClick={() => onSetup(p)}>
              edit connection
            </button>
          </>
        ) : (
          <button type="button" className="sbxp-setup" onClick={() => onSetup(p)}>
            Set up
          </button>
        )}
      </div>
      {error && <div className="sbxp-error">{error}</div>}
    </div>
  );
}

export default function ProviderConfig({ projectId }) {
  const [overview, setOverview] = useState(null);
  const [error, setError] = useState(null);
  const [setupFor, setSetupFor] = useState(null); // provider name while the wizard is open

  const load = useCallback(async () => {
    try {
      setOverview(await api.listSandboxProviders(projectId));
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const onUpdated = (entry) => {
    setOverview((prev) => prev && {
      ...prev,
      providers: prev.providers.map((p) => (p.provider === entry.provider ? entry : p)),
    });
  };

  if (error) return <div className="error-message">{error}</div>;
  if (!overview) return <div className="empty">Loading…</div>;

  const usable = overview.providers.filter((p) => p.setup_complete && p.enabled);
  const active = setupFor
    ? overview.providers.find((p) => p.provider === setupFor)
    : null;
  return (
    <div className="sbxp">
      <p className="sbxp-lede">
        Connect a cloud once, then flip it on — agents procure sandboxes on any
        provider that is set up and enabled.
        {usable.length > 0 && (
          <> Currently open to agents: {usable.map((p) => p.label).join(', ')}.</>
        )}
      </p>
      <div className="sbxp-grid">
        {overview.providers.map((p) => (
          <ProviderCard
            key={p.provider}
            projectId={projectId}
            provider={p}
            onUpdated={onUpdated}
            onSetup={(entry) => setSetupFor(entry.provider)}
          />
        ))}
      </div>
      {active && (
        <ProviderSetupModal
          projectId={projectId}
          entry={active}
          onUpdated={onUpdated}
          onClose={() => setSetupFor(null)}
        />
      )}
    </div>
  );
}

import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import StatusPill from './StatusPill';
import TerminalLog from './TerminalLog';

/**
 * SandboxTerminal — a window into one experiment's Modal sandbox.
 *
 * Replaces the old job dashboard. The agent procures the sandbox (sandbox.request
 * over MCP) and runs commands over SSH itself; this panel only *observes*:
 *   - sandbox status + SSH connection details (read-only, copyable);
 *   - a live transcript of every command + output recorded in the sandbox;
 *   - **MLflow** (port 5000) and **TensorBoard** (port 6006) dashboards
 *     surfaced as HTTPS URLs from Modal encrypted tunnels — rendered as
 *     `<iframe>` tabs sitting next to "Terminal" so the user can watch
 *     loss curves, gradients, and TB scalars live without ever opening a
 *     separate tab. Tabs only appear when the row exposes a URL for them.
 *
 * Polls GET /sandbox + /sandbox/terminal every 3s while the sandbox is running.
 */
const PANEL_TABS = [
  { key: 'terminal', label: 'Terminal' },
  { key: 'mlflow', label: 'MLflow' },
  { key: 'tensorboard', label: 'TensorBoard' },
];
const RUNNING = 'running';
const PROVISIONING = 'provisioning';
const FAILED = 'failed';

export default function SandboxTerminal({ projectId, experimentId }) {
  const [sandbox, setSandbox] = useState(null);
  const [transcript, setTranscript] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [error, setError] = useState(null);
  const [releasing, setReleasing] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  // Active observability tab. Terminal is always available; mlflow/tensorboard
  // only render when their URL is non-empty on the row. If the active tab loses
  // its URL (e.g. the sandbox was released), fall back to terminal.
  const [activeTab, setActiveTab] = useState('terminal');

  const fetchOnce = useCallback(async () => {
    try {
      const sb = await api.getSandbox(projectId, experimentId);
      setSandbox(sb);
      setError(null);
      if (sb && sb.sandbox_id) {
        try {
          const term = await api.getSandboxTerminal(projectId, experimentId);
          setTranscript(term.transcript || '');
        } catch {
          /* terminal is best-effort */
        }
      }
      if (sb && sb.status === RUNNING) {
        try {
          setMetrics(await api.getSandboxMetrics(projectId, experimentId));
        } catch {
          /* live usage is best-effort */
        }
      } else {
        setMetrics(null);
      }
    } catch (err) {
      setError(err.message);
    }
  }, [projectId, experimentId]);

  useEffect(() => {
    let cancelled = false;
    fetchOnce();
    const tick = () => { if (!cancelled) fetchOnce(); };
    const t = setInterval(tick, 3000);
    return () => { cancelled = true; clearInterval(t); };
  }, [fetchOnce]);

  const onRelease = useCallback(async () => {
    setReleasing(true);
    try {
      await api.releaseSandbox(projectId, experimentId);
      await fetchOnce();
    } catch (err) {
      setError(err.message);
    } finally {
      setReleasing(false);
    }
  }, [projectId, experimentId, fetchOnce]);

  const status = sandbox?.status || 'none';
  const isLive = status === RUNNING;
  const isProvisioning = status === PROVISIONING;
  const isFailed = status === FAILED;
  const hasPanel = status !== 'none';

  return (
    <section className="sbx" id="execution">
      <header className="sbx-head">
        <div className="cluster" style={{ gap: 8 }}>
          <span className="sbx-title">Sandbox terminal</span>
          {hasPanel && <StatusPill value={status} />}
          {isLive && <span className="log-tail-live-dot" title="live" />}
        </div>
        {(isLive || isProvisioning) && (
          <button className="btn btn--sm btn--ghost" onClick={onRelease} disabled={releasing}>
            {releasing ? 'Releasing…' : isProvisioning ? 'Cancel' : 'Release sandbox'}
          </button>
        )}
      </header>

      {error && <div className="error-message">{error}</div>}

      {!hasPanel ? (
        <div className="sbx-empty">
          No sandbox for this experiment yet. The agent provisions one with{' '}
          <span className="mono">sandbox.request</span> and then runs commands over SSH.
        </div>
      ) : isProvisioning ? (
        <div className="sbx-provisioning">
          <span className="log-tail-live-dot" title="provisioning" />
          <div>
            <div className="sbx-provisioning-title">
              Provisioning{sandbox.phase ? ` · ${sandbox.phase}` : ''}
            </div>
            <div className="sbx-provisioning-detail">
              {sandbox.detail || 'Setting up the sandbox (sync → create → SSH)…'}
            </div>
          </div>
        </div>
      ) : isFailed ? (
        <div className="sbx-failed">
          <div className="sbx-failed-title">Provisioning failed</div>
          <div className="sbx-failed-detail mono">{sandbox.error || 'unknown error'}</div>
          <div className="sbx-failed-hint">
            The agent can call <span className="mono">sandbox.request</span> to retry.
          </div>
        </div>
      ) : (
        <>
          <SandboxMeta sandbox={sandbox} />
          <SandboxUsage metrics={metrics} sandbox={sandbox} />
          <SandboxPanelTabs
            sandbox={sandbox}
            transcript={transcript}
            isLive={isLive}
            showRaw={showRaw}
            setShowRaw={setShowRaw}
            activeTab={activeTab}
            setActiveTab={setActiveTab}
          />
        </>
      )}
    </section>
  );
}

/**
 * SandboxPanelTabs — switches between the terminal transcript and the in-sandbox
 * observability dashboards (MLflow + TensorBoard). The dashboard URLs come from
 * the row's `dashboards` map (HTTPS tunnels Modal exposes for encrypted_ports).
 * A tab whose URL is empty is hidden; if the currently-active tab disappears
 * (sandbox released, tunnel relocation lost the URL temporarily), we fall back
 * to Terminal so the panel never goes blank.
 */
function SandboxPanelTabs({
  sandbox,
  transcript,
  isLive,
  showRaw,
  setShowRaw,
  activeTab,
  setActiveTab,
}) {
  const dashboards = sandbox?.dashboards || {};
  const availableTabs = PANEL_TABS.filter(
    (t) => t.key === 'terminal' || dashboards[t.key],
  );
  const effectiveTab = availableTabs.some((t) => t.key === activeTab)
    ? activeTab
    : 'terminal';

  return (
    <div className="sbx-tabs">
      <div className="sbx-tabs-strip" role="tablist">
        {availableTabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={effectiveTab === tab.key}
            className={`sbx-tab${effectiveTab === tab.key ? ' is-active' : ''}`}
            onClick={() => setActiveTab(tab.key)}
            title={
              tab.key === 'mlflow'
                ? 'MLflow tracking server (port 5000) — runs, metrics, params, artifacts'
                : tab.key === 'tensorboard'
                ? 'TensorBoard (port 6006) — scalars and event-file visualizations'
                : 'Recorded SSH command transcript'
            }
          >
            {tab.label}
            {tab.key !== 'terminal' && isLive && (
              <span className="log-tail-live-dot" title="live" />
            )}
          </button>
        ))}
        <div className="sbx-tabs-spacer" />
        {effectiveTab === 'terminal' && transcript && transcript.trim() !== '' && (
          <button
            type="button"
            className="sbx-term-toggle"
            onClick={() => setShowRaw((v) => !v)}
            title={showRaw ? 'Show formatted view' : 'Show raw transcript'}
          >
            {showRaw ? 'formatted' : 'raw'}
          </button>
        )}
        {effectiveTab !== 'terminal' && dashboards[effectiveTab] && (
          <a
            href={dashboards[effectiveTab]}
            target="_blank"
            rel="noreferrer noopener"
            className="sbx-term-toggle"
            title="Open this dashboard in a new tab"
          >
            open ↗
          </a>
        )}
      </div>

      {effectiveTab === 'terminal' ? (
        <SandboxTerminalPane
          transcript={transcript}
          isLive={isLive}
          showRaw={showRaw}
        />
      ) : (
        <SandboxDashboardFrame
          name={effectiveTab}
          url={dashboards[effectiveTab]}
        />
      )}
    </div>
  );
}

function SandboxTerminalPane({ transcript, isLive, showRaw }) {
  return (
    <>
      <div className="sbx-term-head">
        <span>
          terminal transcript
          {transcript != null && ` · ${transcript.split('\n').length} lines`}
        </span>
      </div>
      {transcript == null ? (
        <div className="log-tail-empty">Loading transcript…</div>
      ) : transcript.trim() === '' ? (
        <div className="log-tail-empty">
          No commands recorded yet. Output appears here as the agent runs commands over SSH.
        </div>
      ) : (
        <TerminalLog text={transcript} live={isLive} raw={showRaw} />
      )}
    </>
  );
}

/**
 * SandboxDashboardFrame — embeds an MLflow or TensorBoard dashboard served from
 * inside the sandbox. The iframe is sandboxed (allow-scripts + same-origin) but
 * not allowed top-navigation or forms, so a malicious page inside the iframe
 * can't redirect the user away. The wrapper key includes the URL so a tunnel
 * relocation forces a clean reload rather than reusing a now-404 src.
 */
function SandboxDashboardFrame({ name, url }) {
  if (!url) {
    return (
      <div className="log-tail-empty">
        No {name} dashboard URL on this sandbox yet. The dashboard server
        comes up shortly after SSH; refreshes on the next poll.
      </div>
    );
  }
  const title = name === 'mlflow' ? 'MLflow tracking UI' : 'TensorBoard';
  return (
    <div className="sbx-dashboard" key={url}>
      <iframe
        src={url}
        title={title}
        className="sbx-dashboard-frame"
        sandbox="allow-scripts allow-same-origin allow-popups allow-downloads"
        loading="lazy"
        referrerPolicy="no-referrer"
      />
    </div>
  );
}

function SandboxMeta({ sandbox }) {
  const [copied, setCopied] = useState(false);
  const host = sandbox.ssh_host;
  const port = sandbox.ssh_port;
  const user = sandbox.ssh_user || 'root';
  const command =
    host && port
      ? `ssh -i <key> -p ${port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ${user}@${host}`
      : null;

  function copy() {
    if (!command) return;
    navigator.clipboard?.writeText(command);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="sbx-meta">
      <div className="sbx-meta-row">
        <span className="sbx-meta-key">id</span>
        <span className="mono">{sandbox.sandbox_id}</span>
      </div>
      {(sandbox.gpu || sandbox.cpu || sandbox.memory) && (
        <div className="sbx-meta-row">
          <span className="sbx-meta-key">resources</span>
          <span className="mono">
            {[sandbox.gpu && `gpu ${sandbox.gpu}`, sandbox.cpu && `${sandbox.cpu} cpu`, sandbox.memory && `${sandbox.memory} MiB`]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </div>
      )}
      {host && port && (
        <div className="sbx-meta-row">
          <span className="sbx-meta-key">ssh</span>
          <span className="mono sbx-ssh">{user}@{host}:{port}</span>
          <button className="btn btn--xs btn--ghost" onClick={copy}>{copied ? 'copied' : 'copy cmd'}</button>
        </div>
      )}
      {sandbox.workdir && (
        <div className="sbx-meta-row">
          <span className="sbx-meta-key">workdir</span>
          <span className="mono">{sandbox.workdir}</span>
        </div>
      )}
      {sandbox.expires_at && (
        <div className="sbx-meta-row">
          <span className="sbx-meta-key">expires</span>
          <span className="mono">{sandbox.expires_at}</span>
        </div>
      )}
    </div>
  );
}

/**
 * SandboxUsage — live in-container resource gauges (CPU / RAM / GPU), sampled
 * inside the sandbox every poll. Best-effort: renders nothing until the first
 * successful sample, and a quiet note when the sampler is unavailable (e.g. a
 * CPU-only image without nvidia-smi). Reserved gpu/cpu/memory from the sandbox
 * row frame the bars when the cgroup limit isn't readable.
 */
function SandboxUsage({ metrics, sandbox }) {
  if (!metrics) return null;
  if (metrics.available === false || !metrics.metrics) {
    return (
      <div className="sbx-usage sbx-usage--empty">
        <span className="sbx-usage-title">live usage</span>
        <span className="sbx-usage-note">sampling…</span>
      </div>
    );
  }
  const m = metrics.metrics;
  const reservedMemBytes = sandbox?.memory ? sandbox.memory * 1024 * 1024 : null;

  const cpuUsed = m.cpu?.used_cores;
  const cpuLimit = m.cpu?.limit_cores || sandbox?.cpu || null;
  const memUsed = m.memory?.used_bytes;
  const memLimit = m.memory?.limit_bytes || reservedMemBytes;
  const gpus = Array.isArray(m.gpus) ? m.gpus : [];

  return (
    <div className="sbx-usage">
      <div className="sbx-usage-head">
        <span className="sbx-usage-title">live usage</span>
        <span className="log-tail-live-dot" title="sampled live" />
      </div>
      <div className="sbx-usage-grid">
        {cpuUsed != null && (
          <UsageBar
            label="CPU"
            value={cpuUsed}
            max={cpuLimit}
            pct={cpuLimit ? (cpuUsed / cpuLimit) * 100 : null}
            text={`${cpuUsed.toFixed(2)}${cpuLimit ? ` / ${fmtCores(cpuLimit)}` : ''} cores`}
          />
        )}
        {memUsed != null && (
          <UsageBar
            label="RAM"
            value={memUsed}
            max={memLimit}
            pct={memLimit ? (memUsed / memLimit) * 100 : null}
            text={`${fmtBytes(memUsed)}${memLimit ? ` / ${fmtBytes(memLimit)}` : ''}`}
            title="Resident memory in use (anonymous + unreclaimable). Excludes reclaimable page cache / mmapped files, so it reflects real pressure toward the reserved limit, not what `free` reports."
          />
        )}
        {gpus.map((g) => (
          <UsageBar
            key={`gpu-util-${g.index}`}
            label={gpus.length > 1 ? `GPU${g.index} util` : 'GPU util'}
            pct={g.util_pct}
            text={g.util_pct != null ? `${g.util_pct}%` : '—'}
          />
        ))}
        {gpus.map((g) => (
          g.mem_total_mib ? (
            <UsageBar
              key={`gpu-vram-${g.index}`}
              label={gpus.length > 1 ? `GPU${g.index} VRAM` : 'VRAM'}
              pct={g.mem_used_mib != null ? (g.mem_used_mib / g.mem_total_mib) * 100 : null}
              text={`${fmtMib(g.mem_used_mib)} / ${fmtMib(g.mem_total_mib)}`}
            />
          ) : null
        ))}
      </div>
    </div>
  );
}

function UsageBar({ label, pct, text, title }) {
  const clamped = pct == null ? null : Math.max(0, Math.min(100, pct));
  const hot = clamped != null && clamped >= 90;
  return (
    <div className="sbx-usage-item" title={title || undefined}>
      <div className="sbx-usage-item-head">
        <span className="sbx-usage-label">{label}</span>
        <span className="sbx-usage-value mono">{text}</span>
      </div>
      <div className="sbx-usage-track">
        <div
          className={`sbx-usage-fill${hot ? ' hot' : ''}`}
          style={{ width: clamped == null ? '0%' : `${clamped}%` }}
        />
      </div>
    </div>
  );
}

function fmtCores(n) {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function fmtBytes(bytes) {
  if (bytes == null) return '—';
  const gib = bytes / (1024 ** 3);
  if (gib >= 1) return `${gib.toFixed(gib >= 10 ? 0 : 1)} GiB`;
  const mib = bytes / (1024 ** 2);
  return `${Math.round(mib)} MiB`;
}

function fmtMib(mib) {
  if (mib == null) return '—';
  return fmtBytes(mib * 1024 * 1024);
}

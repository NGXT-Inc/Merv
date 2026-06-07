import { useEffect } from 'react';
import { createPortal } from 'react-dom';

/**
 * VolumeSyncDetailsModal — full drill-in for the repo ↔ Modal Volume sync.
 *
 * Opened from the VolumeSyncIndicator in the sidebar foot. Everything here is
 * derived from the same `modal.sync.*` activity events the indicator already
 * polls — no extra request. The headline is the LAST completed pass: which
 * files moved, in which direction, how many bytes, plus conflicts/errors and a
 * short history of recent passes so the trend is visible.
 *
 * "Files" come from the enriched `modal.sync.pass` payload (engine.py). The list
 * is capped server-side (largest first); `files_total`/`files_truncated` report
 * the full extent. Passes recorded before that enrichment simply have no `files`
 * key and render an explanatory empty state.
 */

const DIR_META = {
  push: { glyph: '↑', label: 'push', cls: 'vsdm-chip--push', title: 'Uploaded local → Volume' },
  pull: { glyph: '↓', label: 'pull', cls: 'vsdm-chip--pull', title: 'Downloaded Volume → local' },
  del_remote: { glyph: '✕', label: 'del · vol', cls: 'vsdm-chip--del', title: 'Deleted on the Volume' },
  del_local: { glyph: '✕', label: 'del · local', cls: 'vsdm-chip--del', title: 'Deleted locally' },
};

const STATUS_KINDS = new Set(['idle', 'active', 'error', 'conflict', 'paused', 'pending']);

export default function VolumeSyncDetailsModal({ open, onClose, events = [], project, meta = {} }) {
  // Close on Escape while open.
  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const passes = events.filter((e) => e.event === 'modal.sync.pass');
  const lastPass = passes.length ? passes[passes.length - 1] : null;
  const lastPassTs = lastPass ? Date.parse(lastPass.ts) : 0;

  // Conflicts surfaced at or after the last pass (the engine emits one event
  // per conflicting path). Dedupe by path, keeping the most recent.
  const conflicts = dedupeByPath(
    events.filter((e) => e.event === 'modal.sync.conflict' && Date.parse(e.ts) >= lastPassTs),
  );

  const lastError = findLast(events, (e) => e.event === 'modal.sync.error');
  const errorActive = lastError && Date.parse(lastError.ts) > lastPassTs;

  const files = Array.isArray(lastPass?.files) ? lastPass.files : [];
  const filesTotal = Number(lastPass?.files_total ?? files.length);
  const truncated = Boolean(lastPass?.files_truncated) || filesTotal > files.length;

  // Whole-project size, from the last pass. Passes recorded before this was
  // added have no `total_bytes`; hide the strip rather than show 0 B.
  const totalBytes = Number(lastPass?.total_bytes);
  const totalFiles = num(lastPass?.total_files);
  const totalRemoteBytes = Number(lastPass?.total_remote_bytes);
  const hasTotal = Number.isFinite(totalBytes);

  const statusKind = STATUS_KINDS.has(meta.statusKind) ? meta.statusKind : 'idle';
  const intervalSec = Number(meta.intervalSec) || 60;

  const body = (
    <div className="vsdm-overlay" onMouseDown={onClose}>
      <div
        className="vsdm"
        role="dialog"
        aria-modal="true"
        aria-label="Volume sync details"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* ---- header ---- */}
        <div className="vsdm-head">
          <div className="vsdm-head-main">
            <span className={`vsdm-pill vsdm-pill--${statusKind}`}>{meta.statusLabel || statusKind}</span>
            <h2 className="vsdm-title">Volume sync</h2>
          </div>
          <button type="button" className="vsdm-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <p className="vsdm-lede">
          Mirrors your local repo <span className="vsdm-arrow">↔</span> the project&rsquo;s Modal Volume
          {meta.volumeName ? (
            <>
              {' '}
              (<code className="vsdm-vol">{meta.volumeName}</code>)
            </>
          ) : null}
          , both ways, every {intervalSec}s.
        </p>

        {/* ---- schedule strip ---- */}
        <div className="vsdm-sched">
          <span>every {intervalSec}s</span>
          <span className="vsdm-dot-sep">·</span>
          <span>
            {meta.probablyRunning
              ? 'due now'
              : meta.nextInSec != null
              ? `next in ${meta.nextInSec}s`
              : 'awaiting first pass'}
          </span>
          {lastPass && (
            <>
              <span className="vsdm-dot-sep">·</span>
              <span title={lastPass.ts}>last {fmtAgo(meta.sinceLastMs)}</span>
            </>
          )}
        </div>

        {/* ---- project size (whole synced project, from the last pass) ---- */}
        {hasTotal && (
          <div className="vsdm-size" title="Total size of all in-sync files (conflicts excluded)">
            <span className="vsdm-size-label">Project size</span>
            <span className="vsdm-size-val">{humanBytes(totalBytes)}</span>
            <span className="vsdm-size-files">
              across {totalFiles} file{totalFiles === 1 ? '' : 's'}
            </span>
            {Number.isFinite(totalRemoteBytes) && totalRemoteBytes !== totalBytes && (
              <span className="vsdm-size-remote" title="Total size on the Modal Volume">
                <span className="vsdm-dot-sep">·</span>
                volume {humanBytes(totalRemoteBytes)}
              </span>
            )}
          </div>
        )}

        {/* ---- error banner ---- */}
        {errorActive && (
          <div className="vsdm-banner vsdm-banner--err">
            <strong>{lastError.phase || 'sync error'}</strong>
            <span>{lastError.message || lastError.exc_message || 'Unknown error.'}</span>
          </div>
        )}

        {lastPass ? (
          <>
            {/* ---- last-pass summary cards ---- */}
            <div className="vsdm-section-label">Last sync pass</div>
            <div className="vsdm-cards">
              <Stat
                kind="push"
                glyph="↑"
                label="Pushed"
                count={num(lastPass.pushed)}
                sub={humanBytes(num(lastPass.bytes_pushed))}
              />
              <Stat
                kind="pull"
                glyph="↓"
                label="Pulled"
                count={num(lastPass.pulled)}
                sub={humanBytes(num(lastPass.bytes_pulled))}
              />
              <Stat
                kind="del"
                glyph="✕"
                label="Deleted"
                count={num(lastPass.deleted_remote) + num(lastPass.deleted_local)}
                sub={`${num(lastPass.deleted_remote)} vol · ${num(lastPass.deleted_local)} local`}
              />
              <Stat
                kind={num(lastPass.conflicts) > 0 ? 'conflict' : 'neutral'}
                glyph="⚠"
                label="Conflicts"
                count={num(lastPass.conflicts)}
                sub={num(lastPass.conflicts) > 0 ? 'needs resolve' : 'none'}
              />
              <Stat kind="neutral" glyph="◷" label="Duration" count={null} sub={`${num(lastPass.duration_ms)} ms`} />
            </div>

            {/* ---- per-file table ---- */}
            <div className="vsdm-section-label">
              Files
              {filesTotal > 0 && (
                <span className="vsdm-count-note">
                  {truncated ? ` showing ${files.length} of ${filesTotal} · largest first` : ` ${filesTotal}`}
                </span>
              )}
            </div>
            {files.length > 0 ? (
              <div className="vsdm-table-wrap">
                <table className="vsdm-table">
                  <tbody>
                    {files.map((f, i) => {
                      const dm = DIR_META[f.dir] || { glyph: '•', label: f.dir, cls: '', title: f.dir };
                      return (
                        <tr key={`${f.path}-${i}`}>
                          <td className="vsdm-td-dir">
                            <span className={`vsdm-chip ${dm.cls}`} title={dm.title}>
                              <span className="vsdm-chip-glyph">{dm.glyph}</span>
                              {dm.label}
                            </span>
                          </td>
                          <td className="vsdm-td-path" title={f.path}>
                            {f.path}
                          </td>
                          <td className="vsdm-td-size">{f.dir.startsWith('del') ? '—' : humanBytes(num(f.size))}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : filesTotal === 0 ? (
              <div className="vsdm-empty">Nothing changed in the last pass — both sides were already in sync.</div>
            ) : (
              <div className="vsdm-empty">
                Per-file detail isn&rsquo;t recorded for this pass (it predates file-level reporting). The next
                sync will include it.
              </div>
            )}

            {/* ---- conflicts ---- */}
            {conflicts.length > 0 && (
              <>
                <div className="vsdm-section-label vsdm-section-label--warn">
                  Conflicts<span className="vsdm-count-note"> {conflicts.length}</span>
                </div>
                <div className="vsdm-conflict-note">
                  Both sides changed these since the last agreed state. Sync skips them until resolved — reconcile the
                  files, then they sync on the next pass.
                </div>
                <div className="vsdm-table-wrap">
                  <table className="vsdm-table">
                    <tbody>
                      {conflicts.map((c) => (
                        <tr key={c.path}>
                          <td className="vsdm-td-dir">
                            <span className="vsdm-chip vsdm-chip--conflict" title="Changed on both sides">
                              <span className="vsdm-chip-glyph">⚠</span>conflict
                            </span>
                          </td>
                          <td className="vsdm-td-path" title={c.path}>
                            {c.path}
                          </td>
                          <td className="vsdm-td-size">
                            {c.local?.size_bytes != null ? humanBytes(c.local.size_bytes) : '—'}
                            <span className="vsdm-vs"> / </span>
                            {c.remote?.size_bytes != null ? humanBytes(c.remote.size_bytes) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="vsdm-legend">local / volume size</div>
              </>
            )}

            {/* ---- recent history ---- */}
            {passes.length > 1 && (
              <>
                <div className="vsdm-section-label">Recent passes</div>
                <div className="vsdm-history">
                  {passes
                    .slice(-12)
                    .reverse()
                    .map((p, i) => (
                      <div className="vsdm-hist-row" key={`${p.ts}-${i}`}>
                        <span className="vsdm-hist-ts" title={p.ts}>
                          {fmtClock(p.ts)}
                        </span>
                        <span className="vsdm-hist-counts">
                          <span className="vsdm-hist-up">↑{num(p.pushed)}</span>
                          <span className="vsdm-hist-down">↓{num(p.pulled)}</span>
                          {num(p.deleted_remote) + num(p.deleted_local) > 0 && (
                            <span className="vsdm-hist-del">✕{num(p.deleted_remote) + num(p.deleted_local)}</span>
                          )}
                          {num(p.conflicts) > 0 && <span className="vsdm-hist-conf">⚠{num(p.conflicts)}</span>}
                        </span>
                        <span className="vsdm-hist-bytes">
                          {humanBytes(num(p.bytes_pushed) + num(p.bytes_pulled))}
                        </span>
                        <span className="vsdm-hist-ms">{num(p.duration_ms)}ms</span>
                      </div>
                    ))}
                </div>
              </>
            )}
          </>
        ) : (
          <div className="vsdm-empty vsdm-empty--big">
            No completed sync has been recorded yet. The poller runs every {intervalSec}s; a pass also fires when a
            sandbox is requested or synced.
          </div>
        )}

        {/* ---- footer: exclusions ---- */}
        {project && (
          <div className="vsdm-foot">
            <span className="vsdm-foot-label">Ignored paths</span>
            <span className="vsdm-foot-val">{meta.exclusionsSource || project.sync_exclusions_source || 'default'}</span>
            <span className="vsdm-foot-hint">Edit via “Settings” on the sync card.</span>
          </div>
        )}
      </div>
    </div>
  );

  return createPortal(body, document.body);
}

// --- small presentational helper ------------------------------------------

function Stat({ kind, glyph, label, count, sub }) {
  return (
    <div className={`vsdm-card vsdm-card--${kind}`}>
      <div className="vsdm-card-top">
        <span className="vsdm-card-glyph">{glyph}</span>
        {count != null && <span className="vsdm-card-count">{count}</span>}
      </div>
      <div className="vsdm-card-label">{label}</div>
      {sub != null && <div className="vsdm-card-sub">{sub}</div>}
    </div>
  );
}

// --- helpers ---------------------------------------------------------------

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function findLast(arr, pred) {
  for (let i = arr.length - 1; i >= 0; i--) {
    if (pred(arr[i])) return arr[i];
  }
  return null;
}

function dedupeByPath(evs) {
  const byPath = new Map();
  for (const ev of evs) {
    const prev = byPath.get(ev.path);
    if (!prev || Date.parse(ev.ts) >= Date.parse(prev.ts)) byPath.set(ev.path, ev);
  }
  return Array.from(byPath.values()).sort((a, b) => (a.path < b.path ? -1 : 1));
}

function humanBytes(n) {
  const b = Number(n);
  if (!Number.isFinite(b) || b <= 0) return '0 B';
  if (b < 1024) return `${b} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let val = b / 1024;
  let u = 0;
  while (val >= 1024 && u < units.length - 1) {
    val /= 1024;
    u += 1;
  }
  return `${val < 10 ? val.toFixed(1) : Math.round(val)} ${units[u]}`;
}

function fmtAgo(ms) {
  if (ms == null || !Number.isFinite(ms)) return '—';
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function fmtClock(ts) {
  const t = Date.parse(ts);
  if (!Number.isFinite(t)) return '—';
  const d = new Date(t);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

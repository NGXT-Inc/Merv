import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useProjectStore, selectProject } from '../store/useProjectStore';
import ResourceContentView from '../components/ResourceContentView';
import ReviewCard from '../components/ReviewCard';
import GraphOutline from './GraphOutline';
import { normalizeLogic, makeLogicDetail } from './graphModel';

const GraphCanvasOverlay = lazy(() => import('./GraphCanvasOverlay'));

/**
 * MobileSynthesisScreen — the reflection wave on a phone.
 *
 * The desktop attention order, restacked for a single scrollable column:
 *   graph (clean outline, with an opt-in interactive canvas) → the reflection
 *   document → the per-lens reflections (tap to expand) → quiet change-spec /
 *   review disclosures → a muted "reflection history" strip to pan back to
 *   older waves. A past wave renders FAITHFULLY from the bytes it pinned (the
 *   per-wave /graph endpoint + `?version=` content), not the living files a
 *   later wave overwrote. Reached by tapping the Now-screen synthesis card.
 */

const TERMINAL_WAVE = new Set(['published', 'abandoned']);

// The prose doc role was renamed synthesis_doc -> reflection_doc; the per-lens
// doc role reflection -> reflection_lens_doc. Resolve each with a fallback so
// waves from before either rename still render.
const REFLECTION_DOC_ROLES = ['reflection_doc', 'synthesis_doc'];
const LENS_DOC_ROLES = ['reflection_lens_doc', 'reflection'];
const PRIMARY_ROLES = new Set(['graph', ...REFLECTION_DOC_ROLES, ...LENS_DOC_ROLES]);

// Known secondary doc roles get a friendly label; anything else is humanized so
// a new backend role still renders as the synthesis model evolves.
const DOC_ROLE_META = {
  change_spec: { label: 'Change spec — belief-state update', order: 0 },
  proposals: { label: "What's next — proposals", order: 1 },
};

// Small status → dot color for the history chips.
const WAVE_DOT = {
  published: 'var(--supports)',
  abandoned: 'var(--faint)',
  synthesis_review: 'var(--qualifies)',
};

function humanizeRole(role) {
  return role.replace(/[_-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function shortDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
  } catch { return ''; }
}

// Resolve each roster lens to its reflection resource for the current attempt.
// reflection_coverage already matched <lens_id>.md → path + pinned version
// server-side (across both lens-doc roles); here we look up the resource id by
// that path so ResourceContentView can render it.
function reflectionsByLens(wave) {
  const byPath = {};
  for (const r of wave?.current_attempt_resources || []) {
    if (LENS_DOC_ROLES.includes(r.association_role) && r.path) byPath[r.path] = r;
  }
  const map = {};
  for (const lens of wave?.reflection_coverage?.lenses || []) {
    const res = lens.path ? byPath[lens.path] : null;
    map[lens.lens_id] = {
      covered: Boolean(lens.covered),
      resourceId: res?.id || null,
      versionId: lens.version_id || res?.association_version_id || null,
      path: lens.path || res?.path || null,
    };
  }
  return map;
}

// Everything a wave associates that isn't graph / reflection_doc / lens doc —
// today just the change_spec, derived from resources so new roles render too.
function secondaryDocs(resources) {
  const seen = new Set();
  const docs = [];
  for (const r of resources) {
    const role = r.association_role;
    if (!role || PRIMARY_ROLES.has(role) || seen.has(role)) continue;
    seen.add(role);
    const meta = DOC_ROLE_META[role] || {};
    docs.push({ role, res: r, label: meta.label || humanizeRole(role), order: meta.order ?? 100 });
  }
  return docs.sort((a, b) => a.order - b.order || a.role.localeCompare(b.role));
}

export default function MobileSynthesisScreen() {
  const project = useProjectStore(selectProject);
  const projectId = project?.id;

  const [data, setData] = useState(null);
  const [graph, setGraph] = useState(null);
  const [pinnedId, setPinnedId] = useState(null); // null = follow the live wave
  const [showCanvas, setShowCanvas] = useState(false);

  const fetchSyntheses = useCallback(async () => {
    if (!projectId) return;
    const d = await api.getSyntheses(projectId).catch(() => null);
    if (d) setData(prev => (JSON.stringify(prev) === JSON.stringify(d) ? prev : d));
  }, [projectId]);

  useEffect(() => { fetchSyntheses(); }, [fetchSyntheses]);

  const waves = data?.syntheses || [];
  const signal = data?.signal || null;
  const hasAnyWave = waves.length > 0;
  // syntheses arrive oldest-first; current = open wave else latest published.
  const currentId = data?.current?.id || (waves.length ? waves[waves.length - 1].id : null);
  const selectedId = (pinnedId && waves.some(w => w.id === pinnedId)) ? pinnedId : currentId;
  const selectedIndex = waves.findIndex(w => w.id === selectedId);
  const wave = selectedIndex >= 0 ? waves[selectedIndex] : null;
  const isCurrent = Boolean(wave && wave.id === currentId);
  const isOpen = Boolean(wave && !TERMINAL_WAVE.has(String(wave.status)));
  const attemptIndex = wave?.attempt_index;

  // The selected wave's pinned graph. Reset on wave/attempt switch so a stale
  // graph never flashes; re-fetch on the live tick while the wave is open.
  const fetchGraph = useCallback(async () => {
    if (!projectId || !selectedId) return;
    const g = await api.getSynthesisGraph(projectId, selectedId).catch(() => null);
    if (g) setGraph(g);
  }, [projectId, selectedId]);

  useEffect(() => {
    setGraph(null);
    fetchGraph();
  }, [fetchGraph, attemptIndex]);

  // Poll both only while the wave is live — terminal waves are immutable.
  useEffect(() => {
    if (!isOpen) return undefined;
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') { fetchSyntheses(); fetchGraph(); }
    }, 8000);
    return () => clearInterval(t);
  }, [isOpen, fetchSyntheses, fetchGraph]);

  const backToCurrent = useCallback(() => setPinnedId(null), []);

  const logicGraph = graph?.available ? graph.graph : null;
  const model = useMemo(() => normalizeLogic(logicGraph), [logicGraph]);
  const graphAvailable = model.nodes.length > 0;
  const refIndex = graph?.ref_index || {};

  const waveResources = wave?.current_attempt_resources || [];
  const reflections = useMemo(() => (wave ? reflectionsByLens(wave) : {}), [wave]);
  const roster = wave?.roster || [];
  const reviews = wave?.reviews || [];
  const reflectionDoc = REFLECTION_DOC_ROLES
    .map(role => waveResources.find(r => r.association_role === role))
    .find(Boolean) || null;
  // Pin every rendered doc to the exact version THIS wave associated. The living
  // files (reflection_doc, change_spec, proposals) are one resource shared across
  // waves, so the server's default "latest" can resolve to another wave's bytes —
  // pinning keeps each wave faithful, the current one included.
  const docVersion = res => res.association_version_id || null;
  const secondary = secondaryDocs(waveResources);

  const header = (
    <header className="page-header msyn-head">
      <div className="page-eyebrow">
        <Link to="/">Now</Link>
        {wave && waves.length > 1 && <> · wave {selectedIndex + 1} of {waves.length}</>}
      </div>
      <h1 className="page-title">Project synthesis</h1>
    </header>
  );

  if (!hasAnyWave) {
    return (
      <div className="page-stage">
        {header}
        <div className="empty-state empty-state--compact">
          <p>No reflection waves yet. They appear once enough experiments finish.</p>
        </div>
        {signal?.hint && <div className="syn-hint">{signal.hint}</div>}
      </div>
    );
  }

  return (
    <div className="page-stage msyn">
      {header}

      {wave && !isCurrent && (
        <div className="msyn-histbanner">
          <span>Viewing an earlier wave (wave {selectedIndex + 1}).</span>
          <button type="button" className="msyn-histback" onClick={backToCurrent}>
            Current →
          </button>
        </div>
      )}

      {/* 1 — the graph, front and center */}
      <section className="section msyn-graphsec">
        <div className="msyn-eyebrow-row">
          <div className="msyn-eyebrow">Logic graph</div>
          {graphAvailable && (
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => setShowCanvas(true)}>
              View as graph ⤢
            </button>
          )}
        </div>
        {graphAvailable ? (
          <GraphOutline nodes={model.nodes} edges={model.edges} renderDetail={makeLogicDetail(refIndex)} />
        ) : (
          <div className="empty-state empty-state--compact">
            <p>{isOpen ? 'Graph still building…' : 'No graph in this wave.'}</p>
          </div>
        )}
      </section>

      {/* 2 — the reflection document, prominent (with its images) */}
      {reflectionDoc && (
        <section className="section msyn-doc">
          <div className="msyn-eyebrow">Reflection</div>
          <ResourceContentView
            projectId={projectId}
            resourceId={reflectionDoc.id}
            path={reflectionDoc.path}
            version={docVersion(reflectionDoc)}
            hideSource
            stripTitle
          />
        </section>
      )}

      {/* 3 — the per-lens reflections that fed the reflection doc */}
      {roster.length > 0 && (
        <section className="section">
          <div className="msyn-eyebrow">Lens reflections · {roster.length}</div>
          <div className="msyn-lenses">
            {roster.map(lens => (
              <MobileLensRow
                key={lens.id}
                projectId={projectId}
                lens={lens}
                reflection={reflections[lens.id]}
              />
            ))}
          </div>
        </section>
      )}

      {/* secondary, quiet: change spec + other docs, then the review */}
      {secondary.length > 0 && (
        <section className="section">
          {secondary.map(({ role, res, label }) => (
            <MobileDisclosure key={role} label={label}>
              <ResourceContentView
                projectId={projectId}
                resourceId={res.id}
                path={res.path}
                version={docVersion(res)}
                hideSource
              />
            </MobileDisclosure>
          ))}
        </section>
      )}
      {reviews.length > 0 && (
        <section className="section">
          <MobileDisclosure label="Synthesis review" count={reviews.length}>
            {reviews.map(r => <ReviewCard key={r.id} review={r} bare />)}
          </MobileDisclosure>
        </section>
      )}

      {/* version control — muted, pan back to older waves */}
      {waves.length > 1 && (
        <section className="section msyn-history">
          <div className="msyn-eyebrow msyn-eyebrow--muted">Reflection history</div>
          <div className="mchips msyn-waves" role="tablist" aria-label="Reflection waves">
            {[...waves].reverse().map((w, i) => {
              const n = waves.length - i;
              const status = String(w.status || '');
              const isSel = w.id === selectedId;
              const isCur = w.id === currentId;
              return (
                <button
                  key={w.id}
                  type="button"
                  role="tab"
                  aria-selected={isSel}
                  className={`mchip msyn-wave${isSel ? ' active' : ''}${status === 'abandoned' ? ' msyn-wave--faded' : ''}`}
                  onClick={() => setPinnedId(w.id === currentId ? null : w.id)}
                >
                  <span
                    className="msyn-wave-dot"
                    style={{ background: WAVE_DOT[status] || 'var(--active)' }}
                    aria-hidden="true"
                  />
                  <span className="msyn-wave-label">
                    Wave {n}{isCur ? ' · now' : ''}
                  </span>
                  <span className="msyn-wave-meta">
                    {shortDate(w.published_at || w.created_at)}
                  </span>
                </button>
              );
            })}
          </div>
          {wave?.revision_context && (
            <div className="msyn-revision">↩ {wave.revision_context}</div>
          )}
        </section>
      )}

      {signal?.hint && <div className="syn-hint">{signal.hint}</div>}

      {showCanvas && graphAvailable && (
        <Suspense fallback={<div className="gcanvas-overlay gcanvas-overlay--loading">Loading graph…</div>}>
          <GraphCanvasOverlay
            title={logicGraph?.title || 'Project graph'}
            nodes={model.nodes}
            edges={model.edges}
            onClose={() => setShowCanvas(false)}
          />
        </Suspense>
      )}
    </div>
  );
}

// One roster lens + the reflection its subagent wrote, as a tap-to-expand row.
// The reflection markdown is lazy-mounted on open so five lenses don't fire five
// content fetches at once; each wave renders the exact version it pinned.
function MobileLensRow({ projectId, lens, reflection }) {
  const [open, setOpen] = useState(false);
  const covered = Boolean(reflection?.covered && reflection?.resourceId);
  return (
    <div className={`msyn-lens${open ? ' is-open' : ''}`}>
      <button
        type="button"
        className="msyn-lens-head"
        onClick={() => covered && setOpen(v => !v)}
        disabled={!covered}
        aria-expanded={open}
      >
        <span className={`msyn-lens-cover${covered ? ' is-on' : ''}`} aria-hidden="true">
          {covered ? '✓' : '○'}
        </span>
        <span className="msyn-lens-main">
          <span className={`msyn-lens-title${lens.core ? ' msyn-lens-title--core' : ''}`}>{lens.title || lens.id}</span>
          {lens.charter && <span className="msyn-lens-charter">{lens.charter}</span>}
        </span>
        {covered && <span className="msyn-lens-chev" aria-hidden="true">{open ? '▾' : '▸'}</span>}
      </button>
      {open && covered && (
        <div className="msyn-lens-body">
          <ResourceContentView
            projectId={projectId}
            resourceId={reflection.resourceId}
            path={reflection.path}
            version={reflection.versionId || null}
            hideSource
            dedupeTitle={lens.title}
          />
        </div>
      )}
      {!covered && <div className="msyn-lens-pending">reflection not submitted yet</div>}
    </div>
  );
}

// Quiet disclosure for the secondary artifacts (change spec, review).
function MobileDisclosure({ label, count, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="msyn-disc">
      <button
        type="button"
        className="msyn-disc-head"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
      >
        <span>{label}{count != null ? ` (${count})` : ''}</span>
        <span className="msyn-disc-chev" aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      {open && <div className="msyn-disc-body">{children}</div>}
    </div>
  );
}

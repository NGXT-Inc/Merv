import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { useProjectStore, selectResources, useProjectHref } from '../store/useProjectStore';
import FSMStrip from '../components/FSMStrip';
import GateBanner from '../components/GateBanner';
import PlanSpotlight from '../components/PlanSpotlight';
import ReportSpotlight from '../components/ReportSpotlight';
import ExperimentMetrics from '../components/ExperimentMetrics';
import SandboxTerminal from '../components/SandboxTerminal';
import MobileGraphSection from './MobileGraphSection';
import ScrubRail from './ScrubRail';
import { Skeleton } from './Skeleton';
import { expName } from '../utils/experiment';

// At a review gate the workflow needs the human — the gate row's 3px index
// goes orange; otherwise it stays steel (neutral workflow info).
const REVIEW_STATES = new Set(['design_review', 'experiment_review']);

/**
 * Mobile experiment detail — one continuous scroll (design handoff, sketch
 * 2b): Status → Plan → Run → Outcomes flow down a single surface, each
 * introduced by a small label and separated by a hairline. No tab strip;
 * the right-edge ScrubRail (experiment-only) is the section index.
 *
 * Heavy panes attach on tap: the terminal (its poller) and the logic graph
 * mount only when opened, so a long scroll never stacks pollers. Read-only:
 * the gate panel shows the server's workflow state without transition
 * buttons (reviews and transitions are the agent's job).
 */
export default function MobileExperimentDetail() {
  const { experimentId } = useParams();
  const px = useProjectHref();
  const projectId = useProjectStore(s => s.projectId);
  const allProjectResources = useProjectStore(selectResources);

  const [statusData, setStatusData] = useState(null);
  const [error, setError] = useState(null);
  const [termOpen, setTermOpen] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);

  const statusRef = useRef(null);
  const planRef = useRef(null);
  const runRef = useRef(null);
  const outcomesRef = useRef(null);
  // Run only exists while a sandbox is attached — no sandbox, no section
  // (and no rail stop): a terminal with nothing to attach to is dead chrome.
  const hasSandbox = (statusData?.sandboxes || []).length > 0;
  const sections = [
    { id: 'status', label: 'Status', ref: statusRef },
    { id: 'plan', label: 'Plan', ref: planRef },
    ...(hasSandbox ? [{ id: 'run', label: 'Run', ref: runRef }] : []),
    { id: 'outcomes', label: 'Outcomes', ref: outcomesRef },
  ];

  const fetchStatus = useCallback(async () => {
    try {
      const data = await api.getExperimentStatus(projectId, experimentId);
      setStatusData(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, [projectId, experimentId]);

  // Navigating experiment→experiment keeps this component mounted; reset so
  // the old experiment never flashes and heavy panes fold back shut.
  useEffect(() => {
    setStatusData(null);
    setError(null);
    setTermOpen(false);
    setGraphOpen(false);
  }, [experimentId]);

  useEffect(() => {
    let cancelled = false;
    fetchStatus();
    const t = setInterval(() => {
      if (!cancelled && document.visibilityState === 'visible') fetchStatus();
    }, 5000);
    const onVis = () => { if (document.visibilityState === 'visible') fetchStatus(); };
    document.addEventListener('visibilitychange', onVis);
    return () => { cancelled = true; clearInterval(t); document.removeEventListener('visibilitychange', onVis); };
  }, [fetchStatus]);

  const experiment = statusData?.experiment;
  const workflow = statusData?.workflow;

  if (error) {
    return (
      <div className="mxd">
        <div className="error-message">{error}</div>
        <Link className="btn" to={px('/experiments')} style={{ marginTop: 12 }}>← Experiments</Link>
      </div>
    );
  }
  if (!experiment) {
    return (
      <div className="mxd">
        <header className="page-header"><Skeleton lines={1} /></header>
        <Skeleton lines={5} />
      </div>
    );
  }

  const currentAttempt = experiment.attempt_index;
  // Closed experiments need no gate panel — the strip already says it, and
  // the FSM's terminal gate only reports what can no longer happen.
  const isClosed = ['complete', 'failed', 'abandoned'].includes(experiment.status);

  // ── Resource partition (same derivation as the desktop detail page) ──
  const currentRes = (experiment.current_attempt_resources || [])
    .slice()
    .sort((a, b) => (a.association_role || '').localeCompare(b.association_role || ''));
  const enrich = (bare) =>
    bare ? (allProjectResources.find(r => r.id === bare.id) || bare) : null;
  const planResBare = currentRes.find(r => r.association_role === 'plan') || null;
  const planRes = enrich(planResBare)
    || allProjectResources.find(r => (r.associations || []).some(
      a => a.target_type === 'experiment' && a.target_id === experimentId && a.role === 'plan',
    )) || null;
  const reportRes = enrich(currentRes.find(r => r.association_role === 'report') || null);

  const allReviews = (experiment.reviews || []).slice().sort((a, b) =>
    (a.created_at || '').localeCompare(b.created_at || ''),
  );
  const designReviews = allReviews.filter(r => (r.role || '').toLowerCase().includes('design'));
  const experimentReviews = allReviews.filter(r => !(r.role || '').toLowerCase().includes('design'));
  const claimCount = Array.isArray(experiment.tested_claims) ? experiment.tested_claims.length : 0;

  return (
    <div className="mxd">
      <header className="page-header">
        <div className="page-eyebrow">
          <Link to={px('/experiments')}>‹ Experiments</Link>
          {' · '}attempt {currentAttempt}
        </div>
        <h1 className="page-title">{expName(experiment)}</h1>
      </header>

      {/* The strip shows where it stands; the gate row in Status shows what
          the server wants next. One statement each — no disclosure duplicate. */}
      <FSMStrip status={experiment.status} />

      {/* ── Status ─────────────────────────────────────────────────── */}
      <section ref={statusRef} className="mxd-section">
        <div className="mml">Status</div>
        {experiment.intent && <p className="mxd-intent">{experiment.intent}</p>}
        {workflow && !isClosed && (
          <div className={`mxd-gate${REVIEW_STATES.has(experiment.status) ? ' mxd-gate--attn' : ''}`}>
            {/* Read-only: no transition buttons on mobile — reviews and
                transitions are the agent's job. */}
            <GateBanner workflow={workflow} />
          </div>
        )}
        <div className="merow-meta" style={{ marginTop: 10 }}>
          <span>{currentRes.length} resource{currentRes.length === 1 ? '' : 's'}</span>
          {claimCount > 0 && <span>tests {claimCount} claim{claimCount === 1 ? '' : 's'}</span>}
          {allReviews.length > 0 && <span>{allReviews.length} review{allReviews.length === 1 ? '' : 's'}</span>}
        </div>
        <LazyRow
          open={graphOpen}
          onOpen={() => setGraphOpen(true)}
          label="graph — tap to load"
        >
          <MobileGraphSection
            projectId={projectId}
            experimentId={experimentId}
            experimentStatus={experiment.status}
            attemptIndex={currentAttempt}
          />
        </LazyRow>
      </section>

      <div className="mbreak" />

      {/* ── Plan ───────────────────────────────────────────────────── */}
      <section ref={planRef} className="mxd-section">
        <div className="mml">Plan</div>
        {planRes ? (
          <PlanSpotlight
            projectId={projectId}
            planResource={planRes}
            designReviews={designReviews}
            attemptIndex={currentAttempt}
            experimentStatus={experiment.status}
            defaultOpen
          />
        ) : (
          <div className="mquiet">no plan synced yet</div>
        )}
      </section>

      <div className="mbreak" />

      {/* ── Run — only while a sandbox is attached; terminal attaches
             (and starts polling) on tap ── */}
      {hasSandbox && (
        <>
          <section ref={runRef} className="mxd-section">
            <div className="mml">Run</div>
            <LazyRow
              open={termOpen}
              onOpen={() => setTermOpen(true)}
              label="terminal — tap to attach"
            >
              <SandboxTerminal projectId={projectId} experimentId={experimentId} readOnly />
            </LazyRow>
          </section>

          <div className="mbreak" />
        </>
      )}

      {/* ── Outcomes ───────────────────────────────────────────────── */}
      <section ref={outcomesRef} className="mxd-section">
        <div className="mml">Outcomes</div>
        {reportRes && (
          <ReportSpotlight
            projectId={projectId}
            reportResource={reportRes}
            experimentReviews={experimentReviews}
            experimentStatus={experiment.status}
          />
        )}
        <ExperimentMetrics
          projectId={projectId}
          experimentId={experimentId}
          refreshKey={`${experiment.status}:${currentAttempt}`}
        />
      </section>

      <ScrubRail sections={sections} />
    </div>
  );
}

// A heavy pane folded into the surface: a quiet disclosure row that mounts
// its children only once opened (preserves the "polls only when open" rule).
function LazyRow({ open, onOpen, label, children }) {
  if (open) return children;
  return (
    <button type="button" className="mterm-row" onClick={onOpen}>
      <span className="mterm-twist" aria-hidden="true">▸</span>
      {label}
    </button>
  );
}

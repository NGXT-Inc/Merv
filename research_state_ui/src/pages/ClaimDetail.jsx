import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { useProjectStore, selectExperiments, selectEventsAll, useProjectHref } from '../store/useProjectStore';
import ObjId from '../components/ObjId';
import StatusPill from '../components/StatusPill';
import Sparkline from '../components/Sparkline';
import { ConfidenceSignal } from '../components/ClaimEvidence';
import { classifyExperiment, outcomeColor, outcomeLabel, outcomeGlyph, claimStatusColor } from '../utils/evidence';
import { expName } from '../utils/experiment';
import { computeClaimShifts, relDays } from '../utils/claimShifts';
import { planLedger, anchorValueOf } from '../utils/metricProfile';
import { readDirectionOverrides } from '../utils/mlflowPrefs';
import { curveValues } from '../utils/metrics';
import { fmtStamp, fmtNum } from '../utils/format';

export default function ClaimDetail() {
  const { claimId } = useParams();
  const px = useProjectHref();
  const projectId = useProjectStore(s => s.projectId);
  const experiments = useProjectStore(selectExperiments);
  const events = useProjectStore(selectEventsAll);
  const [claim, setClaim] = useState(null);
  const [evidence, setEvidence] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setClaim(null);
    setError(null);
    api.getClaim(projectId, claimId)
      .then(c => !cancelled && setClaim(c))
      .catch(err => !cancelled && setError(err.message));
    return () => { cancelled = true; };
  }, [projectId, claimId]);

  // The quantitative layer: MLflow runs of every experiment testing this
  // claim. Optional by design — the outcome list stands alone without it.
  useEffect(() => {
    let cancelled = false;
    setEvidence(null);
    api.getClaimEvidence(projectId, claimId)
      .then(d => !cancelled && setEvidence(d))
      .catch(() => {});
    return () => { cancelled = true; };
  }, [projectId, claimId]);

  const linkedExperiments = experiments.filter(e =>
    Array.isArray(e.tested_claims) && e.tested_claims.some(c => c.id === claimId),
  );

  // The evidence payload mirrors the MLflow overview shape, so the ledger
  // profiler reads it directly: focus metric, direction, and per-run values
  // scoped to just this claim's experiments.
  const quant = useMemo(() => {
    if (!evidence?.mlflow?.configured) return null;
    const plan = planLedger(evidence, { directionOverrides: readDirectionOverrides(projectId) });
    if (!plan.focus || plan.runs.length === 0) return null;
    const strip = plan.strips.find(s => s.key === plan.focus.key);
    if (!strip || strip.values.length === 0) return null;
    const dir = plan.focus.direction;
    const sorted = strip.values.slice().sort((a, b) => (dir < 0 ? a.v - b.v : b.v - a.v));
    const byExp = new Map();
    for (const p of strip.values) {
      const expId = plan.runs[p.i].expId;
      const cur = byExp.get(expId);
      if (!cur || (dir < 0 ? p.v < cur.v : p.v > cur.v)) byExp.set(expId, p);
    }
    const cells = new Map();
    for (const [expId, best] of byExp) {
      const run = plan.runs[best.i];
      const anchor = anchorValueOf(run, plan.focus.key);
      cells.set(expId, {
        value: best.v,
        delta: anchor != null ? best.v - anchor : null,
        anchor,
        rank: sorted.findIndex(p => p.i === best.i) + 1,
        curve: curveValues(run.history?.[plan.focus.key]),
        live: /running/i.test(run.runStatus || ''),
      });
    }
    return { focus: plan.focus, runCount: strip.values.length, cells };
  }, [evidence, projectId]);

  const shifts = useMemo(
    () => computeClaimShifts(events).filter(s => s.claimId === claimId),
    [events, claimId],
  );

  if (error) {
    return (
      <div className="page-stage">
        <div className="error-message">{error}</div>
        <Link className="btn" to={px('/claims')} style={{ marginTop: 12 }}>← Claims</Link>
      </div>
    );
  }
  if (!claim) {
    return <div className="page-stage"><div className="empty">Loading…</div></div>;
  }

  return (
    <div className="page-stage">
      <header className="page-header page-header--lg">
        <div className="page-eyebrow">
          <Link to={px('/claims')}>Claims</Link> · <ObjId id={claim.id} className="page-eyebrow-id" />
        </div>
        <h1 className="page-title page-title--statement">{claim.statement}</h1>
        <div className="claim-entry-meta">
          <StatusPill value={claim.status} />
          <ConfidenceSignal level={claim.confidence} />
          {claim.scope && <span className="claim-entry-scope">scoped to {claim.scope}</span>}
          {claim.created_at && <span>created {fmtStamp(Date.parse(claim.created_at))}</span>}
        </div>
      </header>

      <section className="section" style={{ marginTop: 32 }}>
        <div className="section-title">Evidence</div>
        {quant && (
          <p className="lgd-note">
            measured by <span className="lgd-line-k">{quant.focus.key}</span>
            {' '}({quant.focus.direction < 0 ? 'lower' : 'higher'} is better) across {quant.runCount} recorded run{quant.runCount === 1 ? '' : 's'}
          </p>
        )}
        {linkedExperiments.length === 0 ? (
          <div className="empty">No experiments link to this claim yet.</div>
        ) : (
          <EvidenceHistory experiments={linkedExperiments} quant={quant} />
        )}
      </section>

      {shifts.length > 0 && (
        <section className="section">
          <div className="section-title">Belief history</div>
          <div className="shift-list">
            {shifts.map((s, i) => (
              <div className="shift-row" key={`${s.at}-${i}`}>
                <div className="shift-line">
                  <span className="shift-change">
                    {s.status ? (
                      <>
                        {s.status.from} → <b style={{ color: claimStatusColor(s.status.to) }}>{s.status.to}</b>
                      </>
                    ) : (
                      <>confidence {s.confidence.from} → {s.confidence.to}</>
                    )}
                  </span>
                  <span className="shift-time">{relDays(s.at)}</span>
                </div>
                {s.rationale && <div className="shift-rationale">{s.rationale}</div>}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// Chronological evidence rows: the tested-by list plus what each test cost
// (attempts) and concluded — the "why" behind the claim's current status.
// When the claim's experiments have recorded MLflow runs, each row also
// carries its measured result on the claim's focus metric.
function EvidenceHistory({ experiments, quant }) {
  const px = useProjectHref();
  const ordered = experiments
    .slice()
    .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
  return (
    <ul className="claim-entry-tests">
      {ordered.map(e => {
        const outcome = classifyExperiment(e);
        const attempts = e.attempt_index || 1;
        const started = e.created_at
          ? new Date(e.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })
          : null;
        const conclusion = (e.conclusion || '').trim().split('\n')[0];
        const q = quant?.cells.get(e.id);
        return (
          <li key={e.id} className="ev-row">
            <Link to={px(`/experiments/${e.id}`)} className="claim-exp-line">
              <span className="claim-exp-mark" style={{ color: outcomeColor(outcome) }} aria-hidden="true">
                {outcomeGlyph(outcome)}
              </span>
              <span className="claim-exp-title">{expName(e)}</span>
              <span className="claim-exp-status">{outcomeLabel(outcome)}</span>
            </Link>
            <div className="ev-row-sub">
              {started && <span>{started}</span>}
              {attempts > 1 && <span>· {attempts} attempts</span>}
            </div>
            {q && <QuantCell q={q} focus={quant.focus} runCount={quant.runCount} ledgerHref={px(`/mlflow?focus=${e.id}`)} />}
            {conclusion && <p className="ev-row-why">{conclusion}</p>}
          </li>
        );
      })}
    </ul>
  );
}

// One experiment's best measurement of the claim's focus metric: the value,
// its delta against the run's own baseline, its rank among every run the
// claim has, and the metric's trajectory — with a jump into the ledger.
function QuantCell({ q, focus, runCount, ledgerHref }) {
  const improved = q.delta != null && (focus.direction > 0 ? q.delta > 0 : q.delta < 0);
  return (
    <div className="ev-quant">
      <span className="ev-quant-key">{focus.key}</span>
      <span className="ev-quant-val">{fmtNum(q.value)}</span>
      {q.delta != null && (
        <span className={`ev-quant-delta ${improved ? 'good' : 'bad'}`}>
          {q.delta >= 0 ? '+' : '−'}{fmtNum(Math.abs(q.delta))} vs {fmtNum(q.anchor)}
        </span>
      )}
      <span className="ev-quant-rank">#{q.rank}/{runCount}</span>
      {q.live && <span className="ev-quant-live">live</span>}
      {q.curve.length >= 2 && (
        <span className="ev-quant-spark"><Sparkline points={q.curve} height={18} /></span>
      )}
      <Link className="ev-quant-link" to={ledgerHref}>ledger →</Link>
    </div>
  );
}

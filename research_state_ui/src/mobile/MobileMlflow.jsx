import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useProjectStore } from '../store/useProjectStore';
import { runsFromMetrics } from '../components/RunMetrics';
import Sparkline from '../components/Sparkline';
import { Skeleton } from './Skeleton';
import { fmtNum, fmtAgo } from '../utils/format';
import { goodDirection, curveValues } from '../utils/metrics';

const REFRESH_MS = 60000;

// Curve stroke follows the metric's meaning: green when up is good, steel
// when down is good, muted for neutral signals.
function strokeFor(key) {
  const dir = goodDirection(key);
  if (dir > 0) return 'var(--supports)';
  if (dir < 0) return 'var(--steel)';
  return 'var(--muted)';
}

/**
 * MLflow (mobile) — the project's quantitative ledger on a phone
 * (design_handoff_mobile_redesign, MLflow.dc.html). One scroll: a
 * metric-focus filter to scan a single signal project-wide, then
 * experiment → run → a 2-up curve grid. Reached from the More sheet.
 */
export default function MobileMlflow() {
  const navigate = useNavigate();
  const projectId = useProjectStore(s => s.projectId);
  const [data, setData] = useState(null);
  const [fetchedAt, setFetchedAt] = useState(null);
  const [error, setError] = useState(null);
  const [focus, setFocus] = useState('all');
  const [expanded, setExpanded] = useState(null); // `${runId}:${key}` of the full-size chart

  useEffect(() => {
    if (!projectId) return undefined;
    let cancelled = false;
    const load = () => api.getMlflowOverview(projectId)
      .then(d => { if (!cancelled) { setData(d); setFetchedAt(Date.now()); setError(null); } })
      .catch(e => { if (!cancelled) setError(e.message); });
    load();
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') load();
    }, REFRESH_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [projectId]);

  const experiments = useMemo(() => {
    const list = Array.isArray(data?.experiments) ? data.experiments : [];
    return list
      .map(exp => ({ ...exp, runs: runsFromMetrics(exp.metrics) }))
      .filter(exp => exp.runs.length > 0);
  }, [data]);

  const metricKeys = useMemo(() => {
    const keys = new Set();
    for (const exp of experiments) {
      for (const run of exp.runs) {
        Object.keys(run.history || {}).forEach(k => keys.add(k));
        Object.keys(run.metrics || {}).forEach(k => keys.add(k));
      }
    }
    return [...keys].sort();
  }, [experiments]);

  const runCount = experiments.reduce((n, e) => n + e.runs.length, 0);

  return (
    <div className="mlfm">
      <button type="button" className="mlfm-eyebrow" onClick={() => navigate(-1)}>‹ More</button>
      <h1 className="mtitle-lg">MLflow</h1>

      {error && <div className="mbanner">{error}</div>}
      {!data ? (
        <Skeleton lines={5} />
      ) : !data.mlflow?.configured ? (
        <div className="mquiet">MLflow isn't configured{data.mlflow?.note ? ` — ${data.mlflow.note}` : ''}</div>
      ) : experiments.length === 0 ? (
        <div className="mquiet">no runs recorded yet</div>
      ) : (
        <>
          <div className="mlfm-lede">
            {experiments.length} experiment{experiments.length === 1 ? '' : 's'}
            {' · '}{runCount} run{runCount === 1 ? '' : 's'}
            {' · '}{metricKeys.length} metric{metricKeys.length === 1 ? '' : 's'}
            {fetchedAt ? ` · updated ${fmtAgo(Date.now() - fetchedAt)}` : ''}
          </div>

          <div className="mefilt mlfm-focus" role="tablist" aria-label="Focus on one metric">
            <button
              type="button"
              role="tab"
              aria-selected={focus === 'all'}
              className={focus === 'all' ? 'on' : ''}
              onClick={() => setFocus('all')}
            >
              All
            </button>
            {metricKeys.map(k => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={focus === k}
                className={`mono${focus === k ? ' on' : ''}`}
                onClick={() => setFocus(k)}
              >
                {k}
              </button>
            ))}
          </div>

          {experiments.map(exp => (
            <ExperimentLedger
              key={exp.experiment_id}
              exp={exp}
              focus={focus}
              expanded={expanded}
              onToggleChart={key => setExpanded(prev => (prev === key ? null : key))}
            />
          ))}
        </>
      )}
    </div>
  );
}

function ExperimentLedger({ exp, focus, expanded, onToggleChart }) {
  return (
    <>
      <div className="mlfm-exp">
        <span className="mlfm-exp-name">{exp.name}</span>
        <span className="mlfm-exp-meta">
          {[exp.status, `${exp.runs.length} run${exp.runs.length === 1 ? '' : 's'}`]
            .filter(Boolean).join(' · ')}
        </span>
      </div>
      {exp.runs.map((run, ri) => (
        <RunLedger
          key={run.run_id || ri}
          run={run}
          focus={focus}
          expanded={expanded}
          onToggleChart={onToggleChart}
        />
      ))}
    </>
  );
}

function RunLedger({ run, focus, expanded, onToggleChart }) {
  const history = run.history && typeof run.history === 'object' ? run.history : {};
  const metrics = run.metrics && typeof run.metrics === 'object' ? run.metrics : {};
  let keys = Array.from(new Set([...Object.keys(history), ...Object.keys(metrics)]));
  if (focus !== 'all') keys = keys.filter(k => k === focus);
  if (keys.length === 0) return null;

  const running = /running/i.test(run.status || '');
  const runId = run.run_id || run.run_name;

  return (
    <>
      <div className="mlfm-run">
        <span className="mlfm-run-name">{run.run_name || run.run_id}</span>
        {run.status && (
          <span className="mlfm-run-status" style={{ color: running ? 'var(--supports)' : 'var(--muted)' }}>
            {run.status}
          </span>
        )}
      </div>
      <div className={`mlfm-grid${keys.length === 1 ? ' mlfm-grid--single' : ''}`}>
        {keys.map(key => {
          const values = curveValues(history[key]);
          const summary = metrics[key];
          const final = values.length
            ? values[values.length - 1]
            : (summary && Number.isFinite(summary.last) ? summary.last : null);
          const dir = values.length >= 2 ? Math.sign(values[values.length - 1] - values[0]) : 0;
          const good = goodDirection(key);
          const chartKey = `${runId}:${key}`;
          const big = expanded === chartKey;
          return (
            <button
              type="button"
              key={key}
              className={`mlfm-cell${big ? ' mlfm-cell--big' : ''}`}
              onClick={() => onToggleChart(chartKey)}
              aria-label={`${key} curve — tap to ${big ? 'shrink' : 'enlarge'}`}
            >
              <span className="mlfm-cell-head">
                <span className="mlfm-cell-key">{key}</span>
                <span className="mlfm-cell-val tabular">
                  {fmtNum(final)}
                  {dir !== 0 && good !== 0 && (
                    <span className={`tr ${dir === good ? 'tr--good' : 'tr--bad'}`}>
                      {dir < 0 ? '▼' : '▲'}
                    </span>
                  )}
                </span>
              </span>
              <Sparkline points={values} height={big ? 96 : 26} stroke={strokeFor(key)} />
            </button>
          );
        })}
      </div>
    </>
  );
}

import { useEffect, useState } from 'react';
import { api } from '../api';
import RunMetrics, { runsFromMetrics } from './RunMetrics';

/**
 * ExperimentMetrics — durable MLflow results for one experiment, inline on the
 * detail page. Reads the centralized ledger via GET …/results/metrics (distinct
 * from live sandbox telemetry, which dies with the VM), so it never polls;
 * `refreshKey` re-fetches when the run's lifecycle advances. Renders nothing
 * until a run is recorded — quiet for qualitative or not-yet-run experiments.
 */
export default function ExperimentMetrics({ projectId, experimentId, refreshKey }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.getResultsMetrics(projectId, experimentId)
      .then(d => { if (!cancelled) setData(d); })
      .catch(() => { /* durable: keep last good (or null), no spinner */ });
    return () => { cancelled = true; };
  }, [projectId, experimentId, refreshKey]);

  const runs = runsFromMetrics(data);
  if (runs.length === 0) return null;

  const drillUrl = data.dashboard_experiment_url || null;

  return (
    <section className="results-metrics">
      <div className="results-metrics-head">
        <span className="results-metrics-title">Recorded results</span>
        {drillUrl
          ? <a className="results-metrics-sub" href={drillUrl} target="_blank" rel="noreferrer">Open in MLflow ↗</a>
          : <span className="results-metrics-sub">durable</span>}
      </div>
      <RunMetrics runs={runs} />
    </section>
  );
}

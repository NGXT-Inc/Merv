import Sparkline from './Sparkline';
import { fmtNum } from '../utils/format';
import { curveValues } from '../utils/metrics';

/**
 * The single renderer for the centralized-MLflow read model — one plugin-side
 * view of the ledger, shared by the inline experiment panel and the project
 * MLflow page. Reads the `results_metrics` shape: each run carries a `metrics`
 * summary ({key: {last, min, max}}) and a `history` ({key: [[step, value], …]}).
 */

// A results-metrics payload → the flat list of runs across its experiments.
export function runsFromMetrics(payload) {
  if (!payload || payload.available === false) return [];
  return (Array.isArray(payload.experiments) ? payload.experiments : [])
    .flatMap(e => (Array.isArray(e.runs) ? e.runs : []));
}

export default function RunMetrics({ runs }) {
  return (
    <div className="mlf-runs">
      {runs.map((run, ri) => <RunRow key={run.run_id || ri} run={run} />)}
    </div>
  );
}

function RunRow({ run }) {
  const history = run.history && typeof run.history === 'object' ? run.history : {};
  const metrics = run.metrics && typeof run.metrics === 'object' ? run.metrics : {};
  const params = run.params && typeof run.params === 'object' ? run.params : {};
  // Union of both keyings so a run that logged only a final value (no step
  // history) still shows its number.
  const metricKeys = Array.from(new Set([...Object.keys(history), ...Object.keys(metrics)]));
  const paramEntries = Object.entries(params);

  return (
    <div className="mlf-run">
      <div className="mlf-run-head">
        <span className="mlf-run-name">{run.run_name || run.run_id}</span>
        {run.status && <span className="mlf-run-status">{run.status}</span>}
      </div>
      {metricKeys.length === 0 ? (
        <p className="mlf-empty">No metrics recorded.</p>
      ) : (
        <div className="mlf-curve-grid">
          {metricKeys.map(key => {
            const values = curveValues(history[key]);
            const summary = metrics[key];
            const final = values.length
              ? values[values.length - 1]
              : (summary && Number.isFinite(summary.last) ? summary.last : null);
            return (
              <div className="mlf-curve" key={key}>
                <div className="mlf-curve-head">
                  <span className="mlf-curve-key" title={key}>{key}</span>
                  <span className="mlf-curve-val">{fmtNum(final)}</span>
                </div>
                <Sparkline points={values} height={48} />
              </div>
            );
          })}
        </div>
      )}
      {paramEntries.length > 0 && (
        <div className="mlf-params">
          {paramEntries.map(([k, v]) => (
            <span className="mlf-param" key={k}><span className="mlf-param-k">{k}</span> {String(v)}</span>
          ))}
        </div>
      )}
    </div>
  );
}

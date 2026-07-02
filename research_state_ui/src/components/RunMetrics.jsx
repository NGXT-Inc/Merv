import { useState } from 'react';
import Sparkline from './Sparkline';
import { fmtNum } from '../utils/format';
import { curveValues } from '../utils/metrics';
import { classifyRunMetrics } from '../utils/metricProfile';

/**
 * The single renderer for one experiment's recorded results (results_metrics
 * shape), inline on the experiment detail pages. The resting surface is the
 * verdict alone: headline metrics with their baseline folded into a delta,
 * plus anything alarming (failed exit codes, a live run). Telemetry, healthy
 * diagnostics, and params wait behind one quiet disclosure row.
 */

// A results-metrics payload → the flat list of runs across its experiments.
export function runsFromMetrics(payload) {
  if (!payload || payload.available === false) return [];
  return (Array.isArray(payload.experiments) ? payload.experiments : [])
    .flatMap(e => (Array.isArray(e.runs) ? e.runs : []));
}

export default function RunMetrics({ runs }) {
  return (
    <div className="rr">
      {runs.map((run, ri) => (
        <RunRow key={run.run_id || ri} run={run} showHead={runs.length > 1} />
      ))}
    </div>
  );
}

function RunRow({ run, showHead }) {
  const [open, setOpen] = useState(false);
  const history = run.history && typeof run.history === 'object' ? run.history : {};
  const { headline, telemetry, diagnostics } = classifyRunMetrics({
    metrics: run.metrics || {},
    params: run.params || {},
  });
  const paramEntries = Object.entries(run.params || {});
  const live = run.status && run.status !== 'FINISHED';
  const failing = diagnostics.filter(d => d.v !== 0);
  const quietDiags = diagnostics.filter(d => d.v === 0);

  // With no directional/anchored metric there is no verdict — the telemetry
  // line is the only signal, so it earns the surface.
  const telemetryOnSurface = headline.length === 0;
  const foldedMetrics = (telemetryOnSurface ? 0 : telemetry.length) + quietDiags.length;
  const foldedLabel = [
    foldedMetrics > 0 && `${foldedMetrics} metric${foldedMetrics === 1 ? '' : 's'}`,
    paramEntries.length > 0 && `${paramEntries.length} param${paramEntries.length === 1 ? '' : 's'}`,
  ].filter(Boolean).join(' · ');

  const empty = headline.length + telemetry.length + diagnostics.length === 0;

  return (
    <div className="rr-run">
      {(showHead || live) && (
        <div className="rr-run-head">
          <span className="rr-run-name">{run.run_name || run.run_id}</span>
          {live && <span className="rr-run-status">{run.status}</span>}
        </div>
      )}

      {empty && <p className="mlf-empty">No metrics recorded.</p>}

      {headline.map(({ key, v, direction, anchor }) => {
        const delta = anchor != null ? v - anchor : null;
        const improved = delta != null && (direction > 0 ? delta > 0 : delta < 0);
        const curve = curveValues(history[key]);
        return (
          <div className="rr-head-row" key={key}>
            <span className="rr-key">
              {key}
              {direction !== 0 && <span className="rr-dir"> {direction < 0 ? '↓' : '↑'} good</span>}
            </span>
            <span className="rr-val">{fmtNum(v)}</span>
            {delta != null && (
              <span className={`rr-delta ${improved ? 'good' : 'bad'}`}>
                {delta >= 0 ? '+' : '−'}{fmtNum(Math.abs(delta))}
                <span className="rr-anchor"> vs {fmtNum(anchor)}</span>
              </span>
            )}
            {curve.length >= 2 && (
              <span className="rr-spark"><Sparkline points={curve} height={22} /></span>
            )}
          </div>
        );
      })}

      {telemetryOnSurface && <TelemetryLine items={telemetry} />}

      {/* A non-zero exit code is a verdict, not detail — it stays loud. */}
      {failing.length > 0 && (
        <p className="rr-line">
          {failing.map(({ key, v }, j) => (
            <span key={key}>
              {j > 0 && ' · '}
              <span className="rr-line-k">{key}</span> <span className="rr-chip bad">{fmtNum(v)}</span>
            </span>
          ))}
        </p>
      )}

      {(foldedMetrics > 0 || paramEntries.length > 0) && (
        <>
          <button type="button" className="rr-more" onClick={() => setOpen(v => !v)} aria-expanded={open}>
            {open ? '▾' : '▸'} {foldedLabel}
          </button>
          {open && (
            <>
              {!showHead && !live && (
                <p className="rr-line"><span className="rr-line-k">run</span> {run.run_name || run.run_id}</p>
              )}
              {!telemetryOnSurface && <TelemetryLine items={telemetry} />}
              {quietDiags.length > 0 && (
                <p className="rr-line">
                  {quietDiags.map(({ key, v }, j) => (
                    <span key={key}>
                      {j > 0 && ' · '}
                      <span className="rr-line-k">{key}</span> <span className="rr-chip ok">{fmtNum(v)}</span>
                    </span>
                  ))}
                </p>
              )}
              {paramEntries.length > 0 && (
                <div className="mlf-params">
                  {paramEntries.map(([k, v]) => (
                    <span className="mlf-param" key={k}><span className="mlf-param-k">{k}</span> {String(v)}</span>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

function TelemetryLine({ items }) {
  if (!items.length) return null;
  return (
    <p className="rr-line">
      {items.map(({ key, v }, j) => (
        <span key={key}>{j > 0 && ' · '}<span className="rr-line-k">{key}</span> {fmtNum(v)}</span>
      ))}
    </p>
  );
}

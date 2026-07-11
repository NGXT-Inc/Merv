import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useProjectHref } from '../store/useProjectStore';
import { fmtUsd, fmtHrs } from '../utils/format';
import { densifyDaily } from '../utils/spend';

/**
 * ComputeSpend — what the project's sandboxes have cost so far.
 *
 * Reads /compute-cost (the sandbox-generations ledger: provider price ×
 * runtime, open boxes billing to now), so it keeps counting after the fleet
 * table forgets a terminated VM. Renders nothing until the project has
 * provisioned compute. Modal/local generations carry no price quote; their
 * hours are shown as "unpriced" rather than silently folded into the dollars.
 */

const monthDay = (date) =>
  new Date(date + 'T00:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' });

// Daily spend columns, HTML-positioned like the ledger charts. When nothing
// is priced the bars fall back to hours so the usage shape still shows.
function SpendBars({ daily }) {
  const days = densifyDaily(daily);
  const priced = days.some(d => d.usd > 0);
  const vals = days.map(d => (priced ? d.usd : d.hours));
  const vmax = Math.max(...vals) || 1;
  return (
    <div className="spend-chart">
      <span className="spend-y">{priced ? fmtUsd(vmax) : fmtHrs(vmax)}</span>
      <div className="spend-bars" role="img" aria-label="Daily compute spend">
        {days.map((d, i) => (
          <div
            key={d.date}
            className="spend-bar"
            title={`${monthDay(d.date)} · ${fmtUsd(d.usd)} · ${fmtHrs(d.hours)}`}
          >
            <div className="spend-bar-fill" style={{ height: `${(vals[i] / vmax) * 100}%` }} />
          </div>
        ))}
      </div>
      <div className="spend-xlabs">
        <span>{monthDay(days[0].date)}</span>
        <span>{monthDay(days[days.length - 1].date)}</span>
      </div>
    </div>
  );
}

const MAX_ROWS = 6;

function ExperimentRows({ entries }) {
  const px = useProjectHref();
  const shown = entries.slice(0, MAX_ROWS);
  const rest = entries.slice(MAX_ROWS);
  const priced = shown.some(e => e.usd > 0);
  const val = (e) => (priced ? e.usd : e.hours);
  const max = Math.max(...shown.map(val)) || 1;
  return (
    <div className="spend-rows">
      {shown.map(e => (
        <div className="spend-row" key={e.experiment_id || 'unattributed'}>
          {e.experiment_id ? (
            <Link className="spend-row-name" to={px(`/experiments/${e.experiment_id}`)}>
              {e.experiment_name || e.experiment_id}
            </Link>
          ) : (
            <span className="spend-row-name">unattributed</span>
          )}
          <span className="spend-row-meter">
            <span style={{ width: `${(val(e) / max) * 100}%` }} />
          </span>
          <span className="spend-row-val">{fmtUsd(e.usd)}</span>
          <span className="spend-row-hrs">{fmtHrs(e.hours)}</span>
        </div>
      ))}
      {rest.length > 0 && (
        <div className="spend-row spend-row--more">
          and {rest.length} more · {fmtUsd(rest.reduce((s, e) => s + e.usd, 0))}
        </div>
      )}
    </div>
  );
}

export default function ComputeSpend({ projectId, fleetSignal = '' }) {
  const [spend, setSpend] = useState(null);
  // fleetSignal changes when the sandbox fleet does — the cheap cue to refetch
  // without polling an endpoint whose open generations move with the clock.
  useEffect(() => {
    if (!projectId) return undefined;
    let mounted = true;
    api.getComputeCost(projectId)
      .then(d => { if (mounted) setSpend(d); })
      .catch(() => { if (mounted) setSpend(null); });
    return () => { mounted = false; };
  }, [projectId, fleetSignal]);

  if (!spend || !spend.generations) return null;

  const allUnpriced = spend.total_usd <= 0 && spend.unpriced_hours > 0;
  return (
    <section className="section">
      <div className="section-title">
        Compute spend
        {spend.open_generations > 0 && spend.burn_usd_per_hour > 0 && (
          <span className="section-title-badge">
            <span className="sidebar-live-dot" />{fmtUsd(spend.burn_usd_per_hour)}/hr burning
          </span>
        )}
      </div>
      <div className="stat-grid" style={{ marginBottom: 'var(--space-4)' }}>
        <div className="stat-card">
          <div className="stat-card-key">Total spend</div>
          <div className="stat-card-value tabular">{allUnpriced ? fmtHrs(spend.total_hours) : fmtUsd(spend.total_usd)}</div>
          <div className="stat-card-sub">
            {allUnpriced
              ? 'no provider pricing (Modal/local)'
              : spend.unpriced_hours > 0
                ? `+ ${fmtHrs(spend.unpriced_hours)} unpriced`
                : `${fmtHrs(spend.total_hours)} of compute`}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-card-key">Compute hours</div>
          <div className="stat-card-value tabular">{fmtHrs(spend.total_hours)}</div>
          <div className="stat-card-sub">{spend.generations} generation{spend.generations === 1 ? '' : 's'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-key">Burning now</div>
          <div className="stat-card-value tabular">
            {spend.open_generations > 0 ? `${fmtUsd(spend.burn_usd_per_hour)}/hr` : '—'}
          </div>
          <div className="stat-card-sub">
            {spend.open_generations > 0
              ? `${spend.open_generations} live sandbox${spend.open_generations === 1 ? '' : 'es'}`
              : 'fleet settled'}
          </div>
        </div>
      </div>
      {spend.daily.length > 0 && <SpendBars daily={spend.daily} />}
      {spend.by_experiment.length > 0 && <ExperimentRows entries={spend.by_experiment} />}
    </section>
  );
}

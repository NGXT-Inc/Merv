import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import LogicGraph from './LogicGraph';
import FSMStrip, { SYNTHESIS_STAGES, SYNTHESIS_GATES, SYNTHESIS_TERMINAL } from './FSMStrip';

/**
 * ProjectSynthesisPanel — the project's logic state, on Home.
 *
 * Renders the living project logic graph (GET /syntheses/current/graph)
 * through the same LogicGraph canvas the experiment page uses, plus the
 * reflection-wave lifecycle around it: the synthesis FSM strip and per-lens
 * reflection coverage while a wave is open, and the coverage/staleness
 * signal ("covers X of Y finished experiments", the soft "Consider running
 * a project reflection" hint) once waves have published.
 */
export default function ProjectSynthesisPanel({ projectId }) {
  const [data, setData] = useState(null);
  const [graphAvailable, setGraphAvailable] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const toggleExpand = useCallback(() => setExpanded(v => !v), []);

  const fetchSyntheses = useCallback(async () => {
    try {
      const payload = await api.getSyntheses(projectId);
      setData(prev => (JSON.stringify(prev) === JSON.stringify(payload) ? prev : payload));
    } catch {
      // Non-fatal: Home still works without the panel's metadata.
    }
  }, [projectId]);

  useEffect(() => {
    fetchSyntheses();
    const t = setInterval(fetchSyntheses, 8000);
    return () => clearInterval(t);
  }, [fetchSyntheses]);

  // Same fullscreen affordance as the experiment graphs: Escape or the
  // backdrop collapses, page scroll locks while open.
  useEffect(() => {
    if (!expanded) return undefined;
    const onKey = e => { if (e.key === 'Escape') setExpanded(false); };
    window.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [expanded]);

  const graphFetcher = useCallback(() => api.getProjectLogicGraph(projectId), [projectId]);

  const open = data?.open_synthesis || null;
  const signal = data?.signal || null;
  const hasAnyWave = (data?.syntheses || []).length > 0;
  const coverage = open?.reflection_coverage;
  // The coverage/staleness signal rides in the graph header instead of taking
  // its own heading row — empty until a wave has published.
  const coverageHint = signal?.last_published_at
    ? `covers ${signal.covered_terminal_experiments} of ${signal.terminal_experiments} finished experiments`
    : '';

  return (
    <section className="section" id="project-synthesis">
      {open && (
        <div className="syn-wave">
          <FSMStrip
            status={open.status}
            stages={SYNTHESIS_STAGES}
            gateStates={SYNTHESIS_GATES}
            terminal={SYNTHESIS_TERMINAL}
            ariaLabel="Synthesis lifecycle"
          />
          {open.status === 'reflecting' && coverage && (
            <div className="syn-lenses" title="Each roster lens submits its own reflection before the wave can synthesize">
              {coverage.lenses.map(lens => (
                <span
                  key={lens.lens_id}
                  className={`syn-lens-chip${lens.covered ? ' syn-lens-chip--done' : ''}`}
                >
                  {lens.covered ? '✓' : '○'} {lens.lens_id}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {signal?.hint && <div className="syn-hint">{signal.hint}</div>}

      {expanded && (
        <div className="fig-backdrop" onClick={() => setExpanded(false)} aria-hidden="true" />
      )}
      <LogicGraph
        projectId={projectId}
        fetcher={graphFetcher}
        live={Boolean(open)}
        storyHint={coverageHint}
        problemsGate="submit_synthesis"
        onAvailability={setGraphAvailable}
        expanded={expanded}
        onToggleExpand={toggleExpand}
      />

      {!graphAvailable && (
        <div className="empty-state empty-state--compact">
          {hasAnyWave ? (
            <p>
              A reflection wave is underway but its project graph isn't written
              yet. It will appear here the moment the agent associates it.
            </p>
          ) : (
            <p>
              No project synthesis yet. When enough experiments have finished,
              the agent runs a reflection wave (the{' '}
              <span className="mono">project-reflection</span> skill): five
              lenses read the project, the agent distills them into a 16-node
              project logic graph, and a reviewer checks the story before it
              publishes here.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

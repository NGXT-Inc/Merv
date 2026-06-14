import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import BottomSheet from './BottomSheet';
import GraphOutline from './GraphOutline';
import { normalizeLogic, makeLogicDetail } from './graphModel';

/**
 * MobileSynthesisCard — surfaces the project synthesis (desktop Home only) on
 * the mobile Now screen: a one-line headline that opens the living project
 * logic graph as a GraphOutline in a bottom sheet. docs/MOBILE_UX_REVIEW.md §4.2.
 */
export default function MobileSynthesisCard({ projectId }) {
  const [meta, setMeta] = useState(null);
  const [graph, setGraph] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getSyntheses(projectId).then(d => { if (!cancelled) setMeta(d); }).catch(() => {});
    api.getProjectLogicGraph(projectId).then(d => { if (!cancelled) setGraph(d); }).catch(() => {});
    return () => { cancelled = true; };
  }, [projectId]);

  const g = graph?.available ? graph.graph : null;
  const model = useMemo(() => normalizeLogic(g), [g]);
  const refIndex = graph?.ref_index || {};
  const hasGraph = model.nodes.length > 0;

  const signal = meta?.signal || null;
  const openWave = meta?.open_synthesis || null;
  const hasAnyWave = (meta?.syntheses || []).length > 0 || Boolean(openWave);

  // Render nothing until there's a story or a wave/hint worth a glance.
  if (!hasGraph && !hasAnyWave && !signal?.hint) return null;

  const headline = openWave
    ? `Reflection ${String(openWave.status || 'in progress').replace(/_/g, ' ')}`
    : signal?.last_published_at
      ? `Covers ${signal.covered_terminal_experiments} of ${signal.terminal_experiments} finished experiments`
      : 'Project synthesis';

  return (
    <section className="section">
      <div className="section-title">Synthesis</div>
      <button
        type="button"
        className="mcard"
        onClick={() => hasGraph && setOpen(true)}
        disabled={!hasGraph}
      >
        <div className="mcard-head">
          <div className="mcard-title">{headline}</div>
          {hasGraph && <span className="mcard-glyph" style={{ color: 'var(--mcp)' }} aria-hidden="true">◆</span>}
        </div>
        {signal?.hint && <div className="mcard-sub">{signal.hint}</div>}
        {hasGraph && (
          <div className="mcard-meta">
            <span>{model.nodes.length} nodes · tap to read the project story</span>
          </div>
        )}
      </button>

      <BottomSheet open={open} onClose={() => setOpen(false)} label="Project synthesis" title={g?.title || 'Project synthesis'}>
        {hasGraph && <GraphOutline nodes={model.nodes} edges={model.edges} renderDetail={makeLogicDetail(refIndex)} />}
      </BottomSheet>
    </section>
  );
}

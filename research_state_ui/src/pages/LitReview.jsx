import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useProjectStore } from '../store/useProjectStore';
import { useStreamAwarePoll } from '../store/useEventStream';
import { api } from '../api';
import MarkdownView from '../components/MarkdownView';
import EntityChip from '../components/EntityChip';

/**
 * The living literature review: one continuous document on a raised sheet —
 * General Summary, numbered theme sections (each ending in its own reference
 * list), then the Papers ledger. Citation numbers are stable ledger positions;
 * clicking a reference jumps to its Papers entry, and a paper's section links
 * jump back up. Agents write it through litreview.* tools; this is the read.
 */
export default function LitReview() {
  const projectId = useProjectStore(s => s.projectId);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [closed, setClosed] = useState(() => new Set());
  const [flash, setFlash] = useState(null);
  const etagRef = useRef(null);
  const flashTimer = useRef(null);

  const fetchReview = useCallback(async () => {
    if (!projectId) return;
    try {
      const res = await api.getLitReviewIfChanged(projectId, etagRef.current);
      if (res?.notModified) { setError(null); return; }
      etagRef.current = res?.etag || null;
      setData(res?.data ?? res);
      setError(null);
    } catch (e) {
      setError(e?.message || 'Failed to load the literature review');
    }
  }, [projectId]);

  useStreamAwarePoll(fetchReview, {
    matches: (row) => String(row?.type || '').startsWith('litreview.'),
  });

  useEffect(() => () => clearTimeout(flashTimer.current), []);

  const sections = data?.sections || [];
  const papers = data?.papers || [];
  const papersById = useMemo(() => new Map(papers.map((p) => [p.id, p])), [papers]);
  // Ledger order (created_seq) is the stable citation number for the document.
  const numById = useMemo(() => new Map(papers.map((p, i) => [p.id, i + 1])), [papers]);
  const sectionsById = useMemo(() => new Map(sections.map((s) => [s.id, s])), [sections]);

  const toggle = (id) => {
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const jumpToPaper = (id) => {
    document.getElementById(`paper-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setFlash(id);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), 1700);
  };

  const jumpToSection = (id) => {
    setClosed((prev) => { const next = new Set(prev); next.delete(id); return next; });
    requestAnimationFrame(() => {
      document.getElementById(`lit-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  };

  // A transient poll failure never blanks last-good data — the error screen
  // only shows when there is nothing to render at all.
  if (!data) {
    return <div className="page-stage"><p className="muted">{error || 'Loading…'}</p></div>;
  }

  const empty = !data.summary?.exists && sections.length === 0 && papers.length === 0;
  if (empty) {
    return (
      <div className="page-stage litreview">
        <p className="muted">
          No literature review yet. Agents build it as papers enter the
          project — citing a paper (litreview.cite) and making targeted
          section edits (litreview.edit).
        </p>
      </div>
    );
  }

  return (
    <div className="page-stage litreview">
      <article className="litreview-sheet">
        <div className="litreview-meta">
          <span>{sections.length} section{sections.length === 1 ? '' : 's'}</span>
          <span className="litreview-meta-dot">·</span>
          <span>{papers.length} paper{papers.length === 1 ? '' : 's'}</span>
        </div>

        <section className="litreview-summary">
          <h1>{data.summary?.title || 'General Summary'}</h1>
          {data.summary?.exists === false ? (
            <p className="muted">Not written yet.</p>
          ) : (
            <>
              {data.summary?.tldr ? <p className="litreview-lede">{data.summary.tldr}</p> : null}
              {data.summary?.body ? <MarkdownView text={data.summary.body} /> : null}
            </>
          )}
        </section>

        {sections.map((s, i) => {
          const isOpen = !closed.has(s.id);
          return (
            <section key={s.id} id={`lit-${s.id}`} className="litreview-section">
              <button
                type="button"
                className="litreview-section-head"
                aria-expanded={isOpen}
                onClick={() => toggle(s.id)}
              >
                <span className="litreview-section-num">{i + 1}</span>
                <span className="litreview-section-title">{s.title}</span>
                <span className={'litreview-chevron' + (isOpen ? ' open' : '')}>›</span>
              </button>
              <p className="litreview-tldr">{s.tldr}</p>
              {isOpen && (
                <div className="litreview-body">
                  {s.body ? <MarkdownView text={s.body} /> : <p className="muted">No body yet.</p>}
                  <SectionRefs
                    cited={s.cited_papers || []}
                    papersById={papersById}
                    numById={numById}
                    onJump={jumpToPaper}
                  />
                </div>
              )}
            </section>
          );
        })}

        {papers.length > 0 && (
          <section id="litreview-papers" className="litreview-papers">
            <h2>Papers <span className="litreview-count">{papers.length}</span></h2>
            <div className="litreview-paper-list">
              {papers.map((p) => (
                <PaperCard
                  key={p.id}
                  paper={p}
                  num={numById.get(p.id)}
                  sectionsById={sectionsById}
                  flash={flash === p.id}
                  onJumpToSection={jumpToSection}
                />
              ))}
            </div>
          </section>
        )}
      </article>
    </div>
  );
}

/** The structured reference list a section ends with; entries jump to Papers. */
function SectionRefs({ cited, papersById, numById, onJump }) {
  if (!cited.length) return null;
  return (
    <div className="litreview-refs">
      <div className="litreview-refs-label">References</div>
      <ol>
        {cited.map((c) => {
          const p = papersById.get(c.id);
          const n = numById.get(c.id);
          return (
            <li key={c.id}>
              <button type="button" className="litreview-ref" onClick={() => onJump(c.id)}>
                <span className="litreview-ref-num">{n}</span>
                <span className="litreview-ref-title">{p?.title || c.title || c.url}</span>
                <span className="litreview-ref-meta">{shortAuthors(p)}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function shortAuthors(p) {
  if (!p) return '';
  const first = (p.authors || [])[0];
  const name = first ? first.split(',')[0].trim() : '';
  const etAl = (p.authors || []).length > 1 ? ' et al.' : '';
  return [name && name + etAl, p.year].filter(Boolean).join(', ');
}

const SOURCE_LABEL = { arxiv: 'arXiv', doi: 'DOI' };
const FLAG_LABEL = { manual: 'manual entry', failed: 'fetch failed' };

function PaperCard({ paper: p, num, sectionsById, flash, onJumpToSection }) {
  const links = p.links || [];
  const sectionLinks = links.filter((l) => l.target_type === 'litreview_section');
  const entityLinks = links.filter((l) => l.target_type !== 'litreview_section');
  // The cite note repeats on every link it was recorded with — show it once.
  const notes = [...new Set(links.map((l) => (l.note || '').trim()).filter(Boolean))];
  const source = SOURCE_LABEL[p.source_kind] || hostOf(p.url);

  return (
    <div id={`paper-${p.id}`} className={'litreview-paper-card' + (flash ? ' flash' : '')}>
      <div className="litreview-paper-num">{num}</div>
      <div className="litreview-paper-main">
        <div className="litreview-paper-title-row">
          <a className="litreview-paper-title" href={p.url} target="_blank" rel="noreferrer">
            {p.title || p.url}
          </a>
          {source && <span className="litreview-badge">{source}</span>}
          {p.fetch_status !== 'fetched' && (
            <span className="litreview-badge litreview-badge--flag">
              {FLAG_LABEL[p.fetch_status] || p.fetch_status}
            </span>
          )}
        </div>
        {(p.authors?.length || p.year) ? (
          <div className="litreview-paper-meta">
            {[(p.authors || []).join(' · '), p.year].filter(Boolean).join(' — ')}
          </div>
        ) : null}
        {notes.map((n) => <p key={n} className="litreview-paper-note">{n}</p>)}
        {(sectionLinks.length > 0 || entityLinks.length > 0) && (
          <div className="litreview-paper-links">
            {sectionLinks.map((l) => (
              <button
                key={l.target_id}
                type="button"
                className="litreview-section-link"
                onClick={() => onJumpToSection(l.target_id)}
              >
                § {sectionsById.get(l.target_id)?.title || 'section'}
              </button>
            ))}
            {entityLinks.map((l, i) => (
              <EntityChip key={`${l.target_id}-${i}`} id={l.target_id} compact />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
}

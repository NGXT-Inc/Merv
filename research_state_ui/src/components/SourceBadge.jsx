/**
 * SourceBadge — surfaces the SUBMITTED-vs-LIVE provenance of a rendered
 * artifact. Gated-role files (plan/report/graph) render the submitted pinned
 * snapshot, not the live working-tree file, which confuses users; this makes
 * that visible.
 */
export default function SourceBadge({ source, versionId }) {
  if (!source) return null;

  const label =
    source === 'submitted' ? 'Submitted' :
    source === 'live' ? 'Live' :
    source === 'unavailable' ? 'Unavailable' : source;

  const shortId = versionId ? versionId.slice(-6) : '';

  return (
    <>
      <span className={`src-badge src-badge--${source}`}>
        <span className="src-badge-dot" aria-hidden="true" />
        {label}
        {source === 'submitted' && versionId && (
          <span className="src-badge-version" title={versionId}>v{shortId}</span>
        )}
      </span>
      {source === 'submitted' && (
        <span className="src-badge-note">pinned snapshot the gates lint — edit &amp; re-associate to update</span>
      )}
    </>
  );
}

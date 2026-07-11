import { RawLink } from './AuthedMedia';

/**
 * ContentUnavailable — degraded panel shown when a resource's bytes are not
 * servable in the current mode. The cloud control plane keeps result-role
 * files metadata-only; their bytes live only on the offline data-plane daemon
 * (source: "unavailable"). Optional `fallbackLink` offers an "open raw"
 * escape hatch where one applies (local mode).
 */
export default function ContentUnavailable({ content, fallbackLink = null }) {
  return (
    <div className="content-unavailable">
      <div className="content-unavailable-title">Content unavailable in this mode</div>
      <div className="content-unavailable-detail">{content?.detail || content?.reason}</div>
      {fallbackLink && <RawLink href={fallbackLink.href}>{fallbackLink.label}</RawLink>}
    </div>
  );
}

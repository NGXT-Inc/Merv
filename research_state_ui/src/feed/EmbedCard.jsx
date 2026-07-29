import { useRef, useState } from 'react';
import { feedApi } from './feedApi';
import { useViewport } from '../store/useViewport';
import { useMountedMedia } from './useMountedMedia';

/**
 * An interactive embed in a post's media slot. Rests as a flat poster (kind
 * accent, ▸ affordance, "interactive" microlabel) and mounts the real document
 * either on demand (mobile: tap — bandwidth + jank) or automatically as it
 * nears the viewport (desktop): the HTML is fetched authed as text and
 * rendered through <iframe srcdoc sandbox="allow-scripts"> — never
 * allow-same-origin, so a hostile embed cannot reach the app's origin,
 * storage, or token.
 *
 * The frame unmounts itself once the reader scrolls well past it (an extended
 * viewport band via IntersectionObserver) so a long feed never accumulates
 * live iframes; the poster stays, and on desktop scrolling back into range
 * re-mounts it automatically (the render cache makes this cheap) — unless
 * the user explicitly closed it with ✕, which sticks for the rest of the
 * session (closedByUser).
 */
export default function EmbedCard({ post, projectId }) {
  const isMobile = useViewport();
  const [state, setState] = useState('poster'); // poster | loading | open | error
  const [html, setHtml] = useState('');
  const boxRef = useRef(null);
  const trackedRef = useRef(false);
  const closedByUserRef = useRef(false);

  const open = () => {
    if (!post.embed_url || state === 'loading' || closedByUserRef.current) return;
    setState('loading');
    feedApi.embedText(post.embed_url)
      .then((text) => {
        setHtml(text);
        setState('open');
        if (!trackedRef.current) {
          trackedRef.current = true;
          // Reuses the existing tracked-event allowlist — an embed open is the
          // feed's "viewed the visual" moment, same as an image zoom.
          feedApi.trackFeed(projectId, 'image_viewed', { post_id: post.id }).catch(() => {});
        }
      })
      .catch(() => setState('error'));
  };

  const close = () => { setState('poster'); setHtml(''); };

  const closeByUser = () => {
    closedByUserRef.current = true;
    close();
  };

  useMountedMedia({ isMobile, state, rootRef: boxRef, open, close });

  if (state === 'open') {
    return (
      <div className="postcard-media postcard-embed postcard-embed--open" ref={boxRef}>
        <iframe
          className="postcard-embed-frame"
          srcDoc={html}
          sandbox="allow-scripts"
          loading="lazy"
          title="Interactive embed"
        />
        <button
          type="button"
          className="postcard-embed-close"
          aria-label="Close interactive embed"
          onClick={closeByUser}
        >
          ✕
        </button>
      </div>
    );
  }

  return (
    <div className="postcard-media postcard-embed" ref={boxRef}>
      <button
        type="button"
        className="postcard-embedposter"
        onClick={open}
        disabled={state === 'loading'}
        aria-label="Open interactive embed"
      >
        <span className="postcard-embed-play" aria-hidden="true">▸</span>
        <span className="postcard-embed-label">
          {state === 'loading' ? 'loading…' : state === 'error' ? 'failed to load — tap to retry' : 'interactive'}
        </span>
      </button>
    </div>
  );
}

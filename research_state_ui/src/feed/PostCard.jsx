import { useCallback, useEffect, useRef, useState } from 'react';
import { feedApi } from './feedApi';
import { postTime } from './feedModel';
import Avatar from './Avatar';
import EmbedCard from './EmbedCard';
import Lightbox from './Lightbox';
import LinkCard from './LinkCard';
import PdfPageCard, { pdfPageInfo } from './PdfPageCard';
import ReplyComposer from './ReplyComposer';
import EntityChip from '../components/EntityChip';
import { authorHue } from '../utils/authorIdentity';

// Load a feed media path through an authenticated fetch and expose it as a
// blob: object URL. Needed because hosted control mode serves feed bytes behind
// the Bearer token, which a plain <img src> can't send. Revokes on unmount /
// path change. `failed` lets the card collapse a media box that will never
// fill, instead of leaving a permanently empty slab.
function useAuthedImage(relPath) {
  const [state, setState] = useState({ url: null, failed: false });
  useEffect(() => {
    if (!relPath) { setState({ url: null, failed: false }); return undefined; }
    let active = true;
    let objectUrl = null;
    const controller = new AbortController();
    setState({ url: null, failed: false });
    feedApi.imageObjectUrl(relPath, { signal: controller.signal })
      .then((u) => {
        if (active) { objectUrl = u; setState({ url: u, failed: false }); }
        else { URL.revokeObjectURL(u); }
      })
      .catch(() => { if (active) setState({ url: null, failed: true }); });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [relPath]);
  return state;
}

// Reaction glyphs — solid geometric "instrument marks", not pictograms:
// boost (rounded triangle, "raise this"), watching (fisheye ring + dot),
// ask (typographic question mark). Single currentColor fills so every
// rest/hover/active state is pure CSS color. Kind keys stay fire/eyes/
// question — they are the API contract; only the artwork is abstract.
const GLYPH_VIEWBOX = '0 0 24 24';
const GLYPH_SHAPES = {
  fire: (
    <path
      fill="currentColor"
      d="M12 3.9 6.4 10.1c-.55.6-.12 1.55.68 1.55h3.28v6.8a1.64 1.64 0 0 0 3.28 0v-6.8h3.28c.8 0 1.23-.95.68-1.55L12 3.9Z"
    />
  ),
  eyes: (
    <path
      fill="currentColor" fillRule="evenodd" clipRule="evenodd"
      d="M12 5.6C7.4 5.6 3.6 8.9 2.1 12c1.5 3.1 5.3 6.4 9.9 6.4s8.4-3.3 9.9-6.4c-1.5-3.1-5.3-6.4-9.9-6.4Zm0 9.7a3.3 3.3 0 1 1 0-6.6 3.3 3.3 0 1 1 0 6.6Z"
    />
  ),
  question: (
    <path
      fill="currentColor"
      d="M11.95 4.7c-2.7 0-4.55 1.6-4.8 4.05l2.8.4c.15-1.25.85-1.9 2-1.9 1.1 0 1.8.65 1.8 1.65 0 .85-.45 1.4-1.55 2.2-1.3.95-1.85 1.85-1.85 3.35v.75h2.8v-.45c0-.95.45-1.45 1.6-2.3 1.3-.95 2.05-2 2.05-3.55 0-2.55-1.95-4.2-4.85-4.2ZM11.75 19.55a1.85 1.85 0 1 0 0-3.7 1.85 1.85 0 1 0 0 3.7Z"
    />
  ),
};

function ReactGlyph({ kind }) {
  return (
    <svg viewBox={GLYPH_VIEWBOX} width="16" height="16" aria-hidden="true">
      {GLYPH_SHAPES[kind]}
    </svg>
  );
}

const REACT_KINDS = ['fire', 'eyes', 'question'];
const REACT_LABEL = { fire: 'More like this', eyes: 'Watching this', question: 'Explain this' };

/**
 * One feed post (Feed_PRD.md): identicon + handle + relative time, brief text,
 * an optional single visual (image, static link card, or on-demand interactive
 * embed), a quiet reaction/reply row, and an optional chip linking to the
 * entity it is about. Deliberately low-chrome — content first.
 */
export default function PostCard({
  post,
  projectId,
  onView,
  now,
  grouped = false,
  depth = 0,
  orphan = false,
  onReact,
  onReply,
}) {
  const cardRef = useRef(null);
  const viewedRef = useRef(false);

  // Fire post_viewed once, when the card first enters the viewport.
  useEffect(() => {
    if (!onView || !cardRef.current || viewedRef.current) return;
    const el = cardRef.current;
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting && !viewedRef.current) {
          viewedRef.current = true;
          onView(post.id);
          io.disconnect();
        }
      }
    }, { threshold: 0.5 });
    io.observe(el);
    return () => io.disconnect();
  }, [post.id, onView]);

  const ts = post.created_at ? new Date(post.created_at).getTime() : null;
  const timeLabel = postTime(ts, now);
  const preview = post.link_preview;
  const pdfInfo = pdfPageInfo(post, preview);
  const image = useAuthedImage(post.image_url);
  const linkThumb = useAuthedImage(
    preview && preview.has_image ? preview.image_url : null
  );
  const [imageLoaded, setImageLoaded] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const [composing, setComposing] = useState(false);
  const mediaBtnRef = useRef(null);

  const openZoom = () => {
    setZoomed(true);
    feedApi.trackFeed(projectId, 'image_viewed', { post_id: post.id }).catch(() => {});
  };
  // Stable identity: it sits in the Lightbox effect deps, and this card
  // re-renders every clock tick.
  const closeZoom = useCallback(() => {
    setZoomed(false);
    mediaBtnRef.current?.focus();
  }, []);

  const kind = post.kind || null;
  const researcher = post.author_role === 'researcher';
  const reactions = post.reactions || {};
  const cls = [
    'postcard',
    grouped ? 'postcard--cont' : '',
    kind ? `postcard--${kind}` : '',
    depth > 0 ? 'postcard--child' : '',
    researcher ? 'postcard--researcher' : '',
  ].filter(Boolean).join(' ');

  return (
    <article className={cls} ref={cardRef}>
      {/* A reply whose parent scrolled out of the loaded window still says it
          is answering something, instead of impersonating a root post. */}
      {orphan && <p className="postcard-replyctx">replying to an earlier post</p>}

      {/* A continuation post (same author, moments later) visually drops the
          whole header (avatar + byline) — the missing row is what reads as
          "…and then they added". It stays in the DOM so the article keeps
          its attribution for screen readers. */}
      <header className={`postcard-head${grouped ? ' postcard-head--cont' : ''}`}>
        <Avatar handle={post.author_handle} role={post.author_role} />
        <span
          className="postcard-author"
          style={{ '--author-hue': authorHue(post.author_handle) }}
        >
          {post.author_handle}
        </span>
        {post.author_role && post.author_role !== 'main' && (
          <span className={`postcard-role postcard-role--${post.author_role}`}>{post.author_role}</span>
        )}
        {/* The kind names the accent for non-color users; it survives
            continuation posts because it is per-post, not per-author. */}
        {kind && <span className={`postcard-kind postcard-kind--${kind}`}>{kind}</span>}
        {timeLabel && (
          <span
            className="postcard-time"
            title={Number.isFinite(ts) ? new Date(ts).toLocaleString() : undefined}
          >
            {timeLabel}
          </span>
        )}
      </header>

      {post.text && <p className="postcard-text">{post.text}</p>}

      {/* The media box is reserved as soon as we know a post has an image, so
          the stream never jumps when blobs arrive; it collapses only if the
          fetch actually fails. */}
      {post.image_url && !image.failed && (
        <div className="postcard-media">
          <button
            ref={mediaBtnRef}
            type="button"
            className="postcard-media-btn"
            onClick={openZoom}
            disabled={!image.url}
            aria-label="View image full size"
          >
            {image.url && (
              <img
                src={image.url}
                alt=""
                className={`postcard-image${imageLoaded ? ' is-loaded' : ''}`}
                onLoad={() => setImageLoaded(true)}
              />
            )}
          </button>
        </div>
      )}
      {zoomed && image.url && (
        <Lightbox src={image.url} onClose={closeZoom} />
      )}

      {post.has_embed && post.embed_url && (
        <EmbedCard post={post} projectId={projectId} />
      )}

      {post.link_url && (
        pdfInfo
          ? <PdfPageCard post={post} projectId={projectId} info={pdfInfo} />
          : <LinkCard post={post} preview={preview} thumbUrl={linkThumb.url} projectId={projectId} />
      )}

      {post.ref && (
        <footer className="postcard-foot">
          <EntityChip id={post.ref} className="postcard-ref-chip" />
        </footer>
      )}

      {(onReact || onReply) && (
        <div className="postcard-actions">
          {onReact && REACT_KINDS.map((k) => (
            <button
              key={k}
              type="button"
              className={`postcard-react${reactions[k] ? ' on' : ''}`}
              aria-pressed={Boolean(reactions[k])}
              aria-label={REACT_LABEL[k]}
              data-tip={REACT_LABEL[k]}
              onClick={() => onReact(post, k)}
            >
              <ReactGlyph kind={k} />
            </button>
          ))}
          {onReply && !composing && (
            <button
              type="button"
              className="postcard-replybtn"
              data-tip="Reply"
              onClick={() => setComposing(true)}
            >
              Reply
            </button>
          )}
        </div>
      )}
      {composing && (
        <ReplyComposer
          onSubmit={(text) => onReply(post, text)}
          onClose={() => setComposing(false)}
        />
      )}
    </article>
  );
}

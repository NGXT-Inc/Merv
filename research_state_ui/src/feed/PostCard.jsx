import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { feedApi } from './feedApi';
import { fmtAgo } from '../utils/format';

// Map a post's optional entity ref to the route that shows it. Unknown kinds
// (rver_/rev_/syn_ on desktop) render as a static chip — there is no single
// detail page for them.
function refTarget(ref) {
  if (!ref) return null;
  if (ref.startsWith('exp_')) return { to: `/experiments/${ref}`, label: 'experiment' };
  if (ref.startsWith('claim_')) return { to: `/claims/${ref}`, label: 'claim' };
  if (ref.startsWith('res_')) return { to: `/resources/${ref}`, label: 'resource' };
  return null;
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

/**
 * One feed post (Feed_PRD.md): handle + relative time, brief text, an optional
 * single visual (image or a static unfurled link card), and an optional chip
 * linking to the entity it is about. Deliberately low-chrome — content first.
 */
export default function PostCard({ post, projectId, onView }) {
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

  // fmtAgo expects an elapsed duration (ms), not an absolute timestamp.
  const ts = post.created_at ? new Date(post.created_at).getTime() : null;
  const ago = ts != null ? Date.now() - ts : null;
  const ref = refTarget(post.ref);
  const preview = post.link_preview;

  return (
    <article className="postcard" ref={cardRef}>
      <header className="postcard-head">
        <span className="postcard-author">{post.author_handle}</span>
        {post.author_role && post.author_role !== 'main' && (
          <span className="postcard-role">{post.author_role}</span>
        )}
        {ago != null && <span className="postcard-time">· {fmtAgo(ago)}</span>}
      </header>

      {post.text && <p className="postcard-text">{post.text}</p>}

      {post.image_url && (
        <div className="postcard-media">
          <img
            src={feedApi.mediaUrl(post.image_url)}
            alt=""
            loading="lazy"
            className="postcard-image"
          />
        </div>
      )}

      {post.link_url && preview && !preview.error && (
        <a
          className="postcard-link"
          href={post.link_url}
          target="_blank"
          rel="noopener noreferrer nofollow"
          onClick={() => feedApi.trackFeed(projectId, 'link_clicked', { post_id: post.id }).catch(() => {})}
        >
          {preview.has_image && preview.image_url && (
            <img src={feedApi.mediaUrl(preview.image_url)} alt="" loading="lazy" className="postcard-link-thumb" />
          )}
          <span className="postcard-link-body">
            <span className="postcard-link-host">
              {hostOf(preview.url || post.link_url)}
              {preview.trusted && <span className="postcard-link-trusted" title="known research source">✓</span>}
            </span>
            {preview.title && <span className="postcard-link-title">{preview.title}</span>}
            {preview.description && <span className="postcard-link-desc">{preview.description}</span>}
          </span>
        </a>
      )}

      {post.link_url && (!preview || preview.error) && (
        <a
          className="postcard-link postcard-link--bare"
          href={post.link_url}
          target="_blank"
          rel="noopener noreferrer nofollow"
          onClick={() => feedApi.trackFeed(projectId, 'link_clicked', { post_id: post.id }).catch(() => {})}
        >
          {post.link_url}
        </a>
      )}

      {post.ref && (
        <footer className="postcard-foot">
          {ref ? (
            <Link className="postcard-ref" to={ref.to}>↗ {ref.label}</Link>
          ) : (
            <span className="postcard-ref postcard-ref--static">↗ {post.ref}</span>
          )}
        </footer>
      )}
    </article>
  );
}

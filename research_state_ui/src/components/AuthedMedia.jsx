import { useEffect, useState } from 'react';
import { fetchObjectUrl, mediaNeedsAuth, stripApiBase } from '../api';

/**
 * Authenticated media plumbing for hosted mode.
 *
 * Browsers never attach the Bearer token to <img src>, <iframe src>, or
 * new-tab navigations, so behind hosted auth those surfaces must fetch with
 * the header and use blob: URLs (the feed's proven pattern). On localhost
 * (no token) everything passes through untouched — native URLs, browser
 * caching, zero behavior change.
 */

// Only our own API URLs need the header; data:/external srcs pass through.
function isApiUrl(url) {
  return typeof url === 'string' && stripApiBase(url).startsWith('/api/');
}

// Resolve a (possibly BASE-prefixed) media URL to something an <img>/<iframe>
// can load: the URL itself locally, a blob: URL under hosted auth.
export function useAuthedSrc(url) {
  const needsAuth = mediaNeedsAuth() && isApiUrl(url);
  const [blobUrl, setBlobUrl] = useState(null);
  useEffect(() => {
    if (!needsAuth || !url) return undefined;
    let revoked = false;
    let objectUrl = null;
    fetchObjectUrl(stripApiBase(url))
      .then((created) => {
        if (revoked) URL.revokeObjectURL(created);
        else setBlobUrl((objectUrl = created));
      })
      .catch(() => setBlobUrl(null));
    return () => {
      revoked = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url, needsAuth]);
  if (!needsAuth) return url;
  return blobUrl;
}

// <img> wrapper for render sites that sit behind conditional returns (where
// calling the hook directly would violate the rules of hooks).
export function AuthedImg({ src, alt = '', className }) {
  const authedSrc = useAuthedSrc(src);
  if (!authedSrc) return null;
  return <img className={className} src={authedSrc} alt={alt} />;
}

// "Open raw" affordance: a plain new-tab anchor locally; under hosted auth a
// button that fetches with the header inside the click gesture, then opens
// the blob (user-gesture context keeps popup blockers quiet).
export function RawLink({ href, className = 'btn btn--sm', children }) {
  if (!mediaNeedsAuth() || !isApiUrl(href)) {
    return (
      <a className={className} href={href} target="_blank" rel="noreferrer">
        {children}
      </a>
    );
  }
  const open = async () => {
    try {
      const blobUrl = await fetchObjectUrl(stripApiBase(href));
      window.open(blobUrl, '_blank', 'noopener');
    } catch {
      /* fetch failure already surfaces via the panel's own error state */
    }
  };
  return (
    <button type="button" className={className} onClick={open}>
      {children}
    </button>
  );
}

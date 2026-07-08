import { useEffect, useRef, useState } from 'react';
import { feedApi } from './feedApi';
import { useViewport } from '../store/useViewport';

// Module-level caches, split in two so zooming never re-fetches:
//  - docCache: the parsed pdf.js document, keyed by URL (sans fragment) —
//    the expensive network+parse step, done once per paper.
//  - pageCache: rendered-page raster, keyed by `${url}@${zoom}@${displayWidth}`
//    (displayWidth rounded to the nearest px) — repeat opens/zooms at a
//    container size already seen are instant.
const docCache = new Map(); // fetchUrl -> Promise<PDFDocumentProxy>
const pageCache = new Map(); // `${url}@${zoom}@${displayWidth}` -> { dataUrl, width, height, cssWidth, cssHeight }

// Zoom ladder: 'fit' means the page's CSS width exactly matches the
// container's content width (no horizontal scroll); the numeric steps are
// multiples of that same container width, panned via the scroll container.
const ZOOM_STEPS = ['fit', 1.5, 2, 3];

// Raster resolution is bounded independently of display size: a canvas is
// never rendered wider than this many device pixels, so a 3x zoom on a
// high-dpr display can't blow memory out.
const MAX_RASTER_WIDTH = 4096;

// Content width of the PDF scroll container used before the container has
// been measured (first paint) or if measurement ever fails.
const FALLBACK_CONTAINER_WIDTH = 570;

// A "paper page" link: an arXiv/PDF URL carrying a #page=N fragment (or, more
// loosely, any link that is obviously a PDF — path ends in .pdf, or the
// arXiv /pdf/ convention). The fragment never reaches the server (fragments
// are client-only), so it is read straight off the stored link_url.
export function pdfPageInfo(post, preview) {
  const url = post?.link_url || '';
  if (!url) return null;
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  const path = parsed.pathname;
  const looksLikePdf = /\.pdf$/i.test(path) || /\/pdf\//i.test(path);
  const m = /(?:^|&)page=(\d+)/i.exec(parsed.hash.replace(/^#/, ''));
  const page = m ? parseInt(m[1], 10) : null;
  if (!looksLikePdf && page == null) return null;
  const kind = preview && preview.kind;
  // Backend classification is best-effort (a direct PDF fetch usually blows
  // the HTML-only unfurl size cap and comes back `kind: "page"` with
  // `preview.error` set) — so a PDF-shaped URL qualifies on its own; an
  // explicit non-paper classification from a *successful* unfurl (e.g. a
  // repo) still overrides it.
  if (kind && kind !== 'paper' && kind !== 'page') return null;
  const arxivId = arxivIdFromUrl(url);
  return {
    url,
    page: page || 1,
    title: (preview && preview.title) || titleFromUrl(url, arxivId),
    authors: (preview && preview.authors) || [],
    year: (preview && preview.year) || '',
    arxivId,
    host: hostFromUrl(url),
  };
}

function arxivIdFromUrl(url) {
  const m = /arxiv\.org\/(?:pdf|abs)\/([\w.]+)/i.exec(url);
  return m ? m[1].replace(/v\d+$/, '') : null;
}

function titleFromUrl(url, arxivId) {
  return arxivId ? `arXiv:${arxivId}` : 'PDF document';
}

function hostFromUrl(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

// "Hu, Edward J." / "Edward J. Hu" -> "Hu" — same convention as LinkCard's
// authorLine, but we only need the first author's surname for "X et al.".
function firstAuthorSurname(authors) {
  if (!authors || !authors.length) return '';
  const a = authors[0];
  return (a.includes(',') ? a.split(',')[0] : a.split(' ').pop()).trim();
}

// Byline: "FirstAuthor et al. · 2021 · page 7", each clause present only
// when its data exists, joined with " · " so a missing field never leaves
// a dangling separator.
export function pdfByline(info) {
  const parts = [];
  const surname = firstAuthorSurname(info.authors);
  if (surname) parts.push(info.authors.length > 1 ? `${surname} et al.` : surname);
  if (info.year) parts.push(String(info.year));
  parts.push(`page ${info.page}`);
  return parts.join(' · ');
}

// Fallback strip for non-arxiv / metadata-less PDFs: "arXiv:<id> · page N"
// or "<host> · page N".
export function pdfFallbackLabel(info) {
  const lead = info.arxivId ? `arXiv:${info.arxivId}` : info.host;
  return `${lead} · page ${info.page}`;
}

// Lazily import pdf.js and wire the worker, once per session.
let pdfjsPromise = null;
function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import('pdfjs-dist/build/pdf.mjs').then((pdfjsLib) => {
      if (!pdfjsLib.GlobalWorkerOptions.workerSrc) {
        pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
          'pdfjs-dist/build/pdf.worker.min.mjs',
          import.meta.url,
        ).href;
      }
      return pdfjsLib;
    });
  }
  return pdfjsPromise;
}

// Fetch + parse the PDF once per URL (sans fragment) and cache the in-flight/
// resolved document promise, so every zoom step and re-render of the same
// paper reuses the already-downloaded bytes instead of re-fetching them.
function loadPdfDoc(fetchUrl) {
  let cached = docCache.get(fetchUrl);
  if (!cached) {
    cached = loadPdfjs().then((pdfjsLib) => pdfjsLib.getDocument({ url: fetchUrl }).promise);
    docCache.set(fetchUrl, cached);
    cached.catch(() => docCache.delete(fetchUrl)); // don't pin a failed fetch
  }
  return cached;
}

// Render the target page onto an offscreen canvas at the given zoom step,
// returning a data URL (cheap to hand to <img>, and it survives the
// iframe-less unmount/remount cycle without re-touching the network).
//
// Display size and raster resolution are deliberately decoupled:
//  - CSS display width = containerWidth * (zoom === 'fit' ? 1 : zoom), so
//    "fit" exactly fills the container (no horizontal scroll) and "150%"
//    really is 1.5x the container, matching the zoom label.
//  - Raster width = displayWidth * devicePixelRatio, clamped to
//    MAX_RASTER_WIDTH so a 3x zoom on a high-dpr screen can't blow past a
//    reasonable canvas memory budget. The pdf.js render scale is derived
//    from that raster width against the page's natural (scale: 1) width.
async function renderPdfPage(url, pageNum, zoom, containerWidth) {
  const displayWidth = Math.round(containerWidth * (zoom === 'fit' ? 1 : zoom));
  const cacheKey = `${url}@${zoom}@${displayWidth}`;
  const cached = pageCache.get(cacheKey);
  if (cached) return cached;
  // Strip the fragment — pdf.js fetches the URL itself and a #page=N tail
  // means nothing to a bare GET.
  const fetchUrl = url.split('#')[0];
  const doc = await loadPdfDoc(fetchUrl);
  const clamped = Math.min(Math.max(1, pageNum), doc.numPages);
  const page = await doc.getPage(clamped);
  const naturalWidth = page.getViewport({ scale: 1 }).width;
  const dpr = window.devicePixelRatio || 1;
  const rasterWidth = Math.min(displayWidth * dpr, MAX_RASTER_WIDTH);
  const scale = rasterWidth / naturalWidth;
  const viewport = page.getViewport({ scale });
  const canvas = document.createElement('canvas');
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  const ctx = canvas.getContext('2d');
  await page.render({ canvasContext: ctx, viewport }).promise;
  const cssHeight = displayWidth * (viewport.height / viewport.width);
  const result = {
    dataUrl: canvas.toDataURL('image/png'),
    width: viewport.width,
    height: viewport.height,
    cssWidth: displayWidth,
    cssHeight,
    numPages: doc.numPages,
    clampedPage: clamped,
    zoom,
  };
  pageCache.set(cacheKey, result);
  return result;
}

// Metadata + zoom overlay, laid over the bottom of the rendered page.
// Desktop: hidden at rest, fades in on container hover/focus-within (CSS
// handles the reveal — see .postcard-pdf-overlay in feed.css). Mobile:
// visibility is driven by the `visible` prop (timed reveal + tap-toggle,
// owned by the parent). `persistent` (iframe-fallback state) renders the
// same strip without relying on hover at all, since iframes eat hover
// events on both platforms.
function PdfOverlay({ info, zoom, onZoomIn, onZoomOut, onZoomReset, showZoom, persistent, visible }) {
  const byline = pdfByline(info);
  const zoomLabel = zoom === 'fit' ? 'fit' : `${Math.round(zoom * 100)}%`;
  const cls = [
    'postcard-pdf-overlay',
    persistent ? 'postcard-pdf-overlay--persistent' : '',
    visible ? 'postcard-pdf-overlay--visible' : '',
  ].filter(Boolean).join(' ');
  return (
    <div className={cls}>
      <div className="postcard-pdf-overlay-meta">
        <span className="postcard-pdf-overlay-title">{info.title}</span>
        <span className="postcard-pdf-overlay-byline">{byline}</span>
      </div>
      <div className="postcard-pdf-overlay-actions">
        {showZoom && (
          <div className="postcard-pdf-zoom">
            <button
              type="button"
              className="postcard-pdf-zoom-btn"
              aria-label="Zoom out"
              onClick={onZoomOut}
            >
              −
            </button>
            <button
              type="button"
              className="postcard-pdf-zoom-label"
              aria-label="Reset zoom"
              onClick={onZoomReset}
            >
              {zoomLabel}
            </button>
            <button
              type="button"
              className="postcard-pdf-zoom-btn"
              aria-label="Zoom in"
              onClick={onZoomIn}
            >
              +
            </button>
          </div>
        )}
        <a
          className="postcard-pdf-open"
          href={info.url}
          target="_blank"
          rel="noopener noreferrer nofollow"
          aria-label="Open on arxiv"
        >
          open ↗
        </a>
      </div>
    </div>
  );
}

// Fallback strip for non-arxiv / metadata-less PDFs — no title/authors/year
// to show, so just name what it is.
function PdfFallbackOverlay({ info, persistent, visible }) {
  const cls = [
    'postcard-pdf-overlay',
    persistent ? 'postcard-pdf-overlay--persistent' : '',
    visible ? 'postcard-pdf-overlay--visible' : '',
  ].filter(Boolean).join(' ');
  return (
    <div className={cls}>
      <div className="postcard-pdf-overlay-meta">
        <span className="postcard-pdf-overlay-byline">{pdfFallbackLabel(info)}</span>
      </div>
      <div className="postcard-pdf-overlay-actions">
        <a
          className="postcard-pdf-open"
          href={info.url}
          target="_blank"
          rel="noopener noreferrer nofollow"
          aria-label="Open on arxiv"
        >
          open ↗
        </a>
      </div>
    </div>
  );
}

/**
 * A post that references an arXiv/PDF paper at a specific page (the URL's
 * `#page=N` fragment) renders as a poster first — title + "page N". On
 * mobile it mounts the actual rendered page only on tap (bandwidth + canvas
 * jank); on desktop it auto-mounts as the card nears the viewport, mirroring
 * EmbedCard's prefetch-ahead + viewport-based auto-unmount so a long feed
 * never accumulates live PDF renders or canvases, while a returning scroll
 * re-renders from the module-level page cache almost for free.
 *
 * Primary path: fetch the PDF bytes (arXiv serves permissive CORS on them —
 * verified empirically) and rasterize the target page with pdf.js, crisp and
 * chrome-free. If that fetch/render fails for any reason (CORS regression,
 * network error, corrupt doc), falls back to the browser's native PDF viewer
 * via an <iframe>, which is not subject to CORS since it's a top-level-ish
 * embed rather than a script-driven fetch.
 *
 * A metadata + zoom overlay sits over the bottom of the rendered page (see
 * PdfOverlay/PdfFallbackOverlay above): title/byline/open-link, plus a
 * fit/1.5x/2x/3x zoom ladder that re-rasters through pdf.js rather than
 * CSS-scaling the existing canvas. Zoom state resets whenever the card
 * auto-unmounts (see `close`).
 */
export default function PdfPageCard({ post, projectId, info }) {
  const isMobile = useViewport();
  const [state, setState] = useState('poster'); // poster | loading | open | error
  const [render, setRender] = useState(null);
  const [zoom, setZoom] = useState('fit'); // 'fit' | 1.5 | 2 | 3
  const [rezooming, setRezooming] = useState(false);
  const [mobileOverlayVisible, setMobileOverlayVisible] = useState(false);
  const boxRef = useRef(null);
  const trackedRef = useRef(false);
  const closedByUserRef = useRef(false);
  const mobileTimerRef = useRef(null);
  const scrollRef = useRef(null);
  const dragRef = useRef(null); // { startX, startY, startLeft, startTop, dragging }

  const hasMeta = Boolean(info.title && (info.authors?.length || info.year));
  const zoomedIn = zoom !== 'fit';

  // The content width available to the PDF (boxRef is the outer card, present
  // in both poster and open states, and the scroll container inside is
  // width:100% of it — so this is the container width without a chicken/egg
  // dependency on the scroll div existing yet). Falls back before first
  // mount / if measurement ever comes back empty.
  const containerWidth = () => boxRef.current?.clientWidth || FALLBACK_CONTAINER_WIDTH;

  const open = () => {
    if (state === 'loading' || closedByUserRef.current) return;
    setState('loading');
    renderPdfPage(info.url, info.page, 'fit', containerWidth())
      .then((r) => {
        setRender(r);
        setState('open');
        if (!trackedRef.current) {
          trackedRef.current = true;
          feedApi.trackFeed(projectId, 'image_viewed', { post_id: post.id }).catch(() => {});
        }
      })
      .catch(() => setState('error'));
  };

  const close = () => {
    setState('poster');
    setZoom('fit');
    setMobileOverlayVisible(false);
    if (mobileTimerRef.current) {
      clearTimeout(mobileTimerRef.current);
      mobileTimerRef.current = null;
    }
  };

  const closeByUser = () => {
    closedByUserRef.current = true;
    close();
  };

  // Re-render at a new zoom step through pdf.js (crisp re-raster, reusing
  // the cached document so this never re-downloads the PDF bytes).
  const applyZoom = (next) => {
    if (next === zoom) return;
    setZoom(next);
    setRezooming(true);
    renderPdfPage(info.url, info.page, next, containerWidth())
      .then((r) => { setRender(r); setRezooming(false); })
      .catch(() => setRezooming(false));
  };

  const zoomIn = () => {
    const idx = ZOOM_STEPS.indexOf(zoom);
    const next = ZOOM_STEPS[Math.min(idx + 1, ZOOM_STEPS.length - 1)];
    applyZoom(next);
  };
  const zoomOut = () => {
    const idx = ZOOM_STEPS.indexOf(zoom);
    const next = ZOOM_STEPS[Math.max(idx - 1, 0)];
    applyZoom(next);
  };
  const zoomReset = () => applyZoom('fit');

  const handleDoubleClick = () => {
    if (isMobile) return;
    zoomIn();
  };

  // Desktop drag-to-pan: mouse-only (touch/pen keep native scrolling — see
  // touch-action below). Only engages when actually zoomed in — gating on
  // the zoom step (rather than raw scrollWidth/Height > client*) avoids a
  // false-positive at 'fit', where the 4:5 card box can be a few px shorter
  // than the rendered page and leave a sub-visible sliver of scrollable
  // overflow. A plain click/dblclick still passes through untouched below
  // the ~4px movement threshold, and gestures starting on the overlay
  // controls are ignored so their own onClick handlers still fire.
  const DRAG_THRESHOLD = 4;
  const handlePointerDown = (e) => {
    if (e.pointerType !== 'mouse' || e.button !== 0) return;
    if (!zoomedIn) return;
    const el = scrollRef.current;
    if (!el) return;
    if (e.target.closest('.postcard-pdf-overlay, .postcard-embed-close')) return;
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      startLeft: el.scrollLeft,
      startTop: el.scrollTop,
      dragging: false,
      pointerId: e.pointerId,
    };
  };
  const handlePointerMove = (e) => {
    const drag = dragRef.current;
    if (!drag || e.pointerId !== drag.pointerId) return;
    const el = scrollRef.current;
    if (!el) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (!drag.dragging) {
      if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
      drag.dragging = true;
      try { el.setPointerCapture(drag.pointerId); } catch { /* pointer already gone */ }
      el.classList.add('postcard-pdf-scroll--dragging');
    }
    e.preventDefault();
    el.scrollLeft = drag.startLeft - dx;
    el.scrollTop = drag.startTop - dy;
  };
  const endDrag = (e) => {
    const drag = dragRef.current;
    if (!drag) return;
    const el = scrollRef.current;
    if (drag.dragging && el) {
      if (el.hasPointerCapture?.(drag.pointerId)) el.releasePointerCapture(drag.pointerId);
      el.classList.remove('postcard-pdf-scroll--dragging');
    }
    dragRef.current = null;
  };

  // Mobile: tapping the rendered page toggles the overlay (after the initial
  // timed reveal has run its course); doesn't interfere with zoom controls,
  // which sit inside the overlay itself.
  const handleMobileTap = () => {
    if (!isMobile) return;
    if (mobileTimerRef.current) {
      clearTimeout(mobileTimerRef.current);
      mobileTimerRef.current = null;
    }
    setMobileOverlayVisible((v) => !v);
  };

  // Mobile: show the overlay for ~2.5s right after the page first renders,
  // then let taps take over.
  useEffect(() => {
    if (!isMobile || state !== 'open') return undefined;
    setMobileOverlayVisible(true);
    mobileTimerRef.current = setTimeout(() => setMobileOverlayVisible(false), 2500);
    return () => {
      if (mobileTimerRef.current) clearTimeout(mobileTimerRef.current);
    };
  }, [isMobile, state, render]);

  // Desktop only: prefetch/auto-mount as the card nears the viewport, and
  // auto-unmount well past it. Mobile keeps strict tap-to-load.
  useEffect(() => {
    if (isMobile || !boxRef.current) return undefined;
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          if (state === 'poster') open();
        } else if (state === 'open') {
          close();
        }
      }
    }, { rootMargin: '200% 0px 200% 0px' });
    io.observe(boxRef.current);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMobile, state]);

  // Mobile: original narrower auto-unmount band for an already tap-opened page.
  useEffect(() => {
    if (!isMobile || state !== 'open' || !boxRef.current) return undefined;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => !e.isIntersecting)) close();
    }, { rootMargin: '150% 0px 150% 0px' });
    io.observe(boxRef.current);
    return () => io.disconnect();
  }, [isMobile, state]);

  if (state === 'open' && render) {
    const cls = [
      'postcard-media',
      'postcard-pdf',
      'postcard-pdf--open',
      zoomedIn ? 'postcard-pdf--zoomed' : '',
    ].filter(Boolean).join(' ');
    return (
      <div className={cls} ref={boxRef} tabIndex={-1}>
        <div
          className="postcard-pdf-scroll"
          ref={scrollRef}
          tabIndex={zoomedIn ? 0 : -1}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        >
          <img
            className="postcard-pdf-canvas"
            src={render.dataUrl}
            alt={`${info.title}, page ${render.clampedPage}`}
            draggable={false}
            onDoubleClick={handleDoubleClick}
            onClick={handleMobileTap}
            style={{
              width: `${render.cssWidth}px`,
              height: `${render.cssHeight}px`,
              opacity: rezooming ? 0.6 : undefined,
            }}
          />
        </div>
        {hasMeta ? (
          <PdfOverlay
            info={info}
            zoom={zoom}
            onZoomIn={zoomIn}
            onZoomOut={zoomOut}
            onZoomReset={zoomReset}
            showZoom
            visible={isMobile ? mobileOverlayVisible : false}
          />
        ) : (
          <PdfFallbackOverlay
            info={info}
            visible={isMobile ? mobileOverlayVisible : false}
          />
        )}
        <button
          type="button"
          className="postcard-embed-close"
          aria-label="Close paper page"
          onClick={closeByUser}
        >
          ✕
        </button>
      </div>
    );
  }

  if (state === 'error') {
    // pdf.js couldn't fetch or render it (CORS regression, transient network
    // failure, malformed doc) — fall back to the native in-browser PDF
    // viewer via iframe, which the SOP allows regardless of CORS. Iframes
    // eat hover events, so the strip here is persistent rather than
    // hover-revealed, and carries no zoom controls (the native viewer has
    // its own).
    return (
      <div className="postcard-media postcard-pdf postcard-pdf--open" ref={boxRef}>
        <iframe
          className="postcard-pdf-frame"
          src={info.url}
          loading="lazy"
          title={info.title}
        />
        {hasMeta
          ? <PdfOverlay info={info} showZoom={false} persistent />
          : <PdfFallbackOverlay info={info} persistent />}
        <button
          type="button"
          className="postcard-embed-close"
          aria-label="Close paper page"
          onClick={closeByUser}
        >
          ✕
        </button>
      </div>
    );
  }

  return (
    <div className="postcard-media postcard-pdf" ref={boxRef}>
      <button
        type="button"
        className="postcard-pdfposter"
        onClick={open}
        disabled={state === 'loading'}
        aria-label={`Open ${info.title}, page ${info.page}`}
      >
        <span className="postcard-pdfposter-title">{info.title}</span>
        <span className="postcard-pdfposter-page">
          {state === 'loading' ? 'loading…' : `page ${info.page}`}
        </span>
      </button>
    </div>
  );
}

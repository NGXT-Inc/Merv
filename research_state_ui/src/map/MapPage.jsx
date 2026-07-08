import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useProjectStore, useProjectHref } from '../store/useProjectStore';
import { getMapState, fetchSnapshotUrl, pinEntity, unpinEntity } from './mapApi';
import './map.css';

/**
 * Research Map (/p/:id/map). The canvas IS the server-rendered PNG — the same
 * pixels the agent snapshot tools return; this page never draws content of its
 * own. Pan/zoom move a client camera (CSS-transforming the last render between
 * debounced refetches, slippy-map style); the /map/state overlay adds
 * hover/click/drag-to-pin as invisible interaction chrome on top.
 */

const ZOOM_MIN = 0.02;
const ZOOM_MAX = 8;
const REFETCH_MS = 220;
const clampZoom = (z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));

function registerFor(registers, zoom) {
  let name = 'L0';
  for (const [candidate, threshold] of registers || []) {
    if (zoom >= threshold) name = candidate;
  }
  return name;
}

function fitViewport(bounds, w, h) {
  const pad = 260;
  const spanX = bounds.max_x - bounds.min_x + pad * 2;
  const spanY = bounds.max_y - bounds.min_y + pad * 2;
  return {
    cx: (bounds.min_x + bounds.max_x) / 2,
    cy: (bounds.min_y + bounds.max_y) / 2,
    zoom: clampZoom(Math.min(w / spanX, h / spanY)),
  };
}

export default function MapPage() {
  const projectId = useProjectStore(s => s.projectId);
  const px = useProjectHref();
  const navigate = useNavigate();
  const stageRef = useRef(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [mapState, setMapState] = useState(null);
  const [vp, setVp] = useState(null);      // camera: { cx, cy, zoom }
  const [img, setImg] = useState(null);    // last render: { url, cx, cy, zoom, w, h }
  const [hoverId, setHoverId] = useState(null);
  const [dragGhost, setDragGhost] = useState(null);
  const [renderTick, setRenderTick] = useState(0);
  const [error, setError] = useState(null);
  const dragRef = useRef(null);
  const pinchRef = useRef(null);
  const hoverTimer = useRef(null);

  // -- camera math -----------------------------------------------------------
  const worldToScreen = useCallback((x, y) => [
    (x - vp.cx) * vp.zoom + size.w / 2,
    (y - vp.cy) * vp.zoom + size.h / 2,
  ], [vp, size]);
  const screenToWorld = useCallback((sx, sy) => [
    vp.cx + (sx - size.w / 2) / vp.zoom,
    vp.cy + (sy - size.h / 2) / vp.zoom,
  ], [vp, size]);

  const zoomAt = useCallback((sx, sy, factor) => {
    setVp(prev => {
      if (!prev) return prev;
      const zoom = clampZoom(prev.zoom * factor);
      const wx = prev.cx + (sx - size.w / 2) / prev.zoom;
      const wy = prev.cy + (sy - size.h / 2) / prev.zoom;
      return { cx: wx - (sx - size.w / 2) / zoom, cy: wy - (sy - size.h / 2) / zoom, zoom };
    });
  }, [size]);

  // -- stage size ------------------------------------------------------------
  useEffect(() => {
    const node = stageRef.current;
    if (!node) return undefined;
    const observer = new ResizeObserver(() => {
      setSize({ w: node.clientWidth, h: node.clientHeight });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // -- board state (positions for hit-testing; fit on first load) -------------
  useEffect(() => {
    let cancelled = false;
    setMapState(null);
    setVp(null);
    setError(null);
    if (!projectId) return undefined;
    getMapState(projectId)
      .then(s => { if (!cancelled) setMapState(s); })
      .catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [projectId]);

  useEffect(() => {
    if (!vp && mapState && size.w > 0) {
      setVp(fitViewport(mapState.bounds, size.w, size.h));
    }
  }, [vp, mapState, size]);

  // -- debounced snapshot refetch (the content) --------------------------------
  useEffect(() => {
    if (!vp || !size.w || !projectId) return undefined;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      const scale = Math.min(2, Math.max(1, Math.round(window.devicePixelRatio || 1)));
      fetchSnapshotUrl(projectId, {
        cx: vp.cx, cy: vp.cy, zoom: vp.zoom, w: size.w, h: size.h, scale,
        signal: controller.signal,
      })
        .then(url => {
          setImg(prev => {
            if (prev) URL.revokeObjectURL(prev.url);
            return { url, ...vp, w: size.w, h: size.h };
          });
          setError(null);
        })
        .catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    }, img ? REFETCH_MS : 0);
    return () => { clearTimeout(timer); controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vp, size, projectId, renderTick]);

  useEffect(() => () => { setImg(prev => { if (prev) URL.revokeObjectURL(prev.url); return null; }); }, [projectId]);

  // -- wheel zoom (non-passive so the page never scrolls under the board) -----
  useEffect(() => {
    const node = stageRef.current;
    if (!node) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const rect = node.getBoundingClientRect();
      zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * 0.0015));
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    return () => node.removeEventListener('wheel', onWheel);
  }, [zoomAt]);

  // -- pan (background) / pinch / drag-to-pin (markers) ------------------------
  const stagePoint = (e) => {
    const rect = stageRef.current.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  // Route the rest of the gesture to the stage. Throws for already-inactive
  // pointers (pen lift between events) — never a reason to drop the gesture.
  const capture = (e) => {
    try { stageRef.current.setPointerCapture(e.pointerId); } catch { /* keep going */ }
  };

  const onStagePointerDown = (e) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    const p = stagePoint(e);
    if (pinchRef.current?.pointers?.size === 1 && e.pointerType === 'touch') {
      // second finger down -> switch to pinch
      const pointers = pinchRef.current.pointers;
      pointers.set(e.pointerId, p);
      const [a, b] = [...pointers.values()];
      pinchRef.current = {
        pointers,
        dist: Math.hypot(a.x - b.x, a.y - b.y),
        zoom: vp.zoom,
      };
      dragRef.current = null;
      return;
    }
    if (e.pointerType === 'touch') {
      pinchRef.current = { pointers: new Map([[e.pointerId, p]]) };
    }
    dragRef.current = { mode: 'pan', start: p, cx: vp.cx, cy: vp.cy };
    capture(e);
  };

  const onMarkerPointerDown = (entity) => (e) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    e.stopPropagation();
    dragRef.current = { mode: 'entity', entity, start: stagePoint(e), moved: false };
    capture(e);
  };

  const onStagePointerMove = (e) => {
    const p = stagePoint(e);
    const pinch = pinchRef.current;
    if (pinch?.pointers?.size === 2) {
      pinch.pointers.set(e.pointerId, p);
      const [a, b] = [...pinch.pointers.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      zoomAt(mid.x, mid.y, (pinch.zoom * (dist / pinch.dist)) / vp.zoom);
      return;
    }
    const drag = dragRef.current;
    if (!drag) return;
    const dx = p.x - drag.start.x;
    const dy = p.y - drag.start.y;
    if (drag.mode === 'pan') {
      setVp(prev => prev && { ...prev, cx: drag.cx - dx / prev.zoom, cy: drag.cy - dy / prev.zoom });
    } else if (drag.mode === 'entity') {
      if (Math.hypot(dx, dy) > 4) drag.moved = true;
      if (drag.moved) setDragGhost(p);
    }
  };

  const onStagePointerUp = async (e) => {
    pinchRef.current?.pointers?.delete(e.pointerId);
    const drag = dragRef.current;
    dragRef.current = null;
    setDragGhost(null);
    if (!drag || drag.mode !== 'entity') return;
    if (!drag.moved) {
      openEntity(drag.entity);
      return;
    }
    const p = stagePoint(e);
    const [wx, wy] = screenToWorld(p.x, p.y);
    try {
      await pinEntity(projectId, drag.entity.id, wx, wy);
      setMapState(await getMapState(projectId));
      setRenderTick(t => t + 1);
    } catch (err) {
      setError(err.message);
    }
  };

  const openEntity = (entity) => {
    if (entity.type === 'experiment') navigate(px(`/experiments/${entity.id}`));
    else if (entity.type === 'claim') navigate(px(`/claims/${entity.id}`));
    else if (entity.type === 'resource') navigate(px(`/resources/${entity.id}`));
    else if (entity.type === 'review') navigate(px('/reviews'));
  };

  const onUnpin = async (entity) => {
    try {
      await unpinEntity(projectId, entity.id);
      setMapState(await getMapState(projectId));
      setRenderTick(t => t + 1);
    } catch (err) {
      setError(err.message);
    }
  };

  const setHover = (id) => {
    clearTimeout(hoverTimer.current);
    if (id) setHoverId(id);
    else hoverTimer.current = setTimeout(() => setHoverId(null), 120);
  };

  // -- render ------------------------------------------------------------------
  const ready = vp && size.w > 0;
  const scaleFactor = img && vp ? vp.zoom / img.zoom : 1;
  const imgStyle = img && vp ? {
    width: img.w,
    height: img.h,
    transform: `translate(${(img.cx - img.w / (2 * img.zoom) - vp.cx) * vp.zoom + size.w / 2}px, `
      + `${(img.cy - img.h / (2 * img.zoom) - vp.cy) * vp.zoom + size.h / 2}px) scale(${scaleFactor})`,
  } : null;
  const hovered = hoverId && mapState?.entities.find(e => e.id === hoverId);
  const hoveredScreen = hovered && ready ? worldToScreen(hovered.x, hovered.y) : null;
  const register = ready && registerFor(mapState?.registers, vp.zoom);

  return (
    <div
      ref={stageRef}
      className={'map-stage' + (dragRef.current?.mode === 'pan' ? ' map-stage--panning' : '')}
      onPointerDown={ready ? onStagePointerDown : undefined}
      onPointerMove={ready ? onStagePointerMove : undefined}
      onPointerUp={ready ? onStagePointerUp : undefined}
      onPointerCancel={ready ? onStagePointerUp : undefined}
    >
      {img && imgStyle && (
        <img className="map-img" src={img.url} alt="Research map" style={imgStyle} draggable={false} />
      )}

      {/* interaction chrome only — the content is the image above */}
      {ready && mapState && mapState.entities.map(entity => {
        const [sx, sy] = worldToScreen(entity.x, entity.y);
        if (sx < -40 || sx > size.w + 40 || sy < -40 || sy > size.h + 40) return null;
        return (
          <div
            key={entity.id}
            className={'map-hit' + (entity.pinned ? ' map-hit--pinned' : '')}
            style={{ left: sx, top: sy }}
            onPointerDown={onMarkerPointerDown(entity)}
            onPointerEnter={() => setHover(entity.id)}
            onPointerLeave={() => setHover(null)}
          />
        );
      })}

      {dragGhost && <div className="map-ghost" style={{ left: dragGhost.x, top: dragGhost.y }} />}

      {hovered && hoveredScreen && !dragGhost && (
        <div
          className="map-tooltip"
          style={{
            left: Math.min(hoveredScreen[0] + 14, size.w - 240),
            top: Math.min(hoveredScreen[1] + 14, size.h - 110),
          }}
          onPointerDown={(e) => e.stopPropagation()}
          onPointerEnter={() => setHover(hovered.id)}
          onPointerLeave={() => setHover(null)}
        >
          <div className="map-tooltip-label">{hovered.label}</div>
          <div className="map-tooltip-meta">
            <span>{hovered.type}</span>
            {hovered.status && <span className="map-tooltip-status">{hovered.status}</span>}
            {hovered.pinned && <span className="map-tooltip-pin">pinned</span>}
          </div>
          <div className="map-tooltip-id mono">{hovered.id}</div>
          {hovered.pinned && (
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => onUnpin(hovered)}>
              Unpin
            </button>
          )}
        </div>
      )}

      {/* pointerdown must not bubble into the stage's pan handler — its
          pointer capture would swallow the button clicks */}
      <div className="map-controls" onPointerDown={(e) => e.stopPropagation()}>
        {register && <span className="map-register" title={`zoom ${vp.zoom.toFixed(2)}`}>{register}</span>}
        <button type="button" className="btn btn--sm" aria-label="Zoom out"
          onClick={() => zoomAt(size.w / 2, size.h / 2, 1 / 1.45)}>−</button>
        <button type="button" className="btn btn--sm" aria-label="Zoom in"
          onClick={() => zoomAt(size.w / 2, size.h / 2, 1.45)}>+</button>
        <button type="button" className="btn btn--sm"
          onClick={() => mapState && setVp(fitViewport(mapState.bounds, size.w, size.h))}>Fit</button>
      </div>

      {!img && !error && <div className="map-status">Rendering the board…</div>}
      {error && <div className="map-status error-message">{error}</div>}
      {mapState && mapState.entities.length === 0 && (
        <div className="map-status empty-state">
          Nothing on the board yet — claims and experiments appear here as they are created.
        </div>
      )}
    </div>
  );
}

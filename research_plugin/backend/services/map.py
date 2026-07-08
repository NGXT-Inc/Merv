"""Research Map service — owns ``map_layout``, the board's spatial memory.

Positions are append-mostly authored knowledge (dev_docs/research_plugin/
RESEARCH_MAP_V1.md): every entity is placed exactly once, deterministically,
and never moves — growth direction IS the progression record. Humans may
drag-to-pin; pinned positions are permanent authored knowledge auto-placement
treats as obstacles. Re-layout is data loss.

Boundary: this service SQLs only its own ``map_layout`` table (registered in
tests/structure/test_module_boundaries.py). Entity, edge, and freshness data
arrive through sibling services' public read APIs injected at composition
(control/record_core.py), never raw SQL against other modules' tables.
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable

from ..domain.map_render import (
    CELL,
    REGISTERS,
    Viewport,
    build_scene,
    parse_cell,
    rasterize,
    register_for_zoom,
)
from ..state.store import BaseStateStore, rows_to_dicts
from ..utils import NotFoundError, ValidationError, now_iso

# Deterministic placement geometry (world units). MIN_SEP is per-kind so small
# docked glyphs (reviews) sit close to their target while cards keep breathing
# room; the required distance between two entities is the mean of their seps.
GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))
# Separation is per-kind and sized to the entity's L3 CARD footprint in world
# units, so cards never overlap once zoomed in. Resources carry the widest
# card, hence the largest separation.
ENTITY_SEP = {"review": 70.0, "resource": 150.0}
DEFAULT_SEP = 200.0
# Claims seed regions; give them generous spacing so clusters read as distinct
# territories at fit-all rather than piling at the origin (real projects carry
# tens of claims). Experiments ring their claim; resources fan into a wide
# multi-ring halo around their experiment (deep-zoom-only vocabulary, but their
# cards must not collide when the reader arrives at L2/L3).
ROOT_SPIRAL_C = 1000.0
REFLECTION_RING_R = 820.0
REFLECTION_RING_STEP = 260.0
CHILD_RING_BASE = {"review": 96.0, "resource": 250.0}
CHILD_RING_DEFAULT = 240.0
CHILD_RING_STEP = 150.0
# Orphan resources (no experiment/reflection association) are shelved in a
# compact grid off the origin instead of scattering as root dots — they are
# library material, not research territory.
SHELF_ORIGIN = (-1500.0, -1500.0)
SHELF_STEP = 110.0
SHELF_COLS = 14

MAX_SIZE = 2400
MIN_SIZE = 160
ZOOM_MIN, ZOOM_MAX = 0.02, 8.0
DEFAULT_SNAPSHOT_SIZE = (1200, 800)
LOCATE_ZOOM = 2.2  # L3: ids legible, the perception -> mutation bridge

# ``parent_id`` freezes the lineage RESOLVED AT PLACEMENT TIME: later link
# edits must not re-home a region (stability is the product), and root
# indexing for the spiral must not depend on re-derivable state.
MAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS map_layout (
  project_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  x REAL NOT NULL,
  y REAL NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0,
  parent_id TEXT NOT NULL DEFAULT '',
  placed_seq INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (project_id, entity_id),
  FOREIGN KEY(project_id) REFERENCES projects(id)
)
"""


def _jitter(entity_id: str) -> float:
    digest = hashlib.sha1(entity_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF * math.tau


def _sep(kind_a: str, kind_b: str) -> float:
    a = ENTITY_SEP.get(kind_a, DEFAULT_SEP)
    b = ENTITY_SEP.get(kind_b, DEFAULT_SEP)
    return (a + b) / 2.0


class MapService:
    def __init__(
        self,
        *,
        store: BaseStateStore,
        experiments: Any,
        claims: Any,
        reflections: Any,
        resources: Any,
        reviews: Any,
        last_touched_reader: Callable[..., dict[str, str]],
    ) -> None:
        self.store = store
        self.experiments = experiments
        self.claims = claims
        self.reflections = reflections
        self.resources = resources
        self.reviews = reviews
        self.last_touched_reader = last_touched_reader
        self._render_cache: OrderedDict[tuple, tuple[bytes, dict[str, Any]]] = OrderedDict()
        with self.store.transaction() as conn:
            conn.execute(MAP_SCHEMA)

    # -- entity collection (sibling read APIs only) ---------------------------

    def _collect(self, *, project_id: str) -> dict[str, Any]:
        """Normalize every board entity + edge from sibling services."""
        experiments = self.experiments.list_experiments(project_id=project_id)["experiments"]
        claims = self.claims.list_claims(project_id=project_id)["claims"]
        reflections = self.reflections.list_reflections(project_id=project_id)["syntheses"]
        # Full (non-compact) hydration: the map needs created_at for the
        # deterministic placement order and associations for parent lineage.
        resources = self.resources.list_resources(project_id=project_id)["resources"]
        reviews = self.reviews.queue(project_id=project_id)["reviews"]
        last_touched = self.last_touched_reader(project_id=project_id)

        entities: dict[str, dict[str, Any]] = {}
        parents: dict[str, str] = {}
        edges: list[dict[str, Any]] = []

        claim_status = {str(c["id"]): str(c.get("status") or "") for c in claims}
        for claim in claims:
            entities[str(claim["id"])] = {
                "id": str(claim["id"]),
                "type": "claim",
                "label": str(claim.get("statement") or claim["id"]),
                "status": str(claim.get("status") or ""),
                "confidence": str(claim.get("confidence") or ""),
                "text": str(claim.get("statement") or ""),
                "created_at": str(claim.get("created_at") or ""),
            }
            # No claim-derivation column exists (verified against the schema):
            # claims root their own regions.
            parents[str(claim["id"])] = ""

        for experiment in experiments:
            exp_id = str(experiment["id"])
            entities[exp_id] = {
                "id": exp_id,
                "type": "experiment",
                "label": str(experiment.get("name") or experiment.get("intent") or exp_id),
                "status": str(experiment.get("status") or ""),
                "attempt_index": int(experiment.get("attempt_index") or 1),
                "text": str(experiment.get("intent") or ""),
                "text2": str(experiment.get("conclusion") or ""),
                "created_at": str(experiment.get("created_at") or ""),
            }
            tested = sorted(
                experiment.get("tested_claims") or [],
                key=lambda c: (str(c.get("created_at") or ""), str(c.get("id") or "")),
            )
            parents[exp_id] = str(tested[0]["id"]) if tested else ""
            for claim in tested:
                claim_id = str(claim["id"])
                kind = "refutes" if claim_status.get(claim_id) == "contradicted" else "tests"
                edges.append({"src": exp_id, "dst": claim_id, "kind": kind})
        for reflection in reflections:
            syn_id = str(reflection["id"])
            entities[syn_id] = {
                "id": syn_id,
                "type": "reflection",
                "label": str(reflection.get("title") or syn_id),
                "status": str(reflection.get("status") or ""),
                "text": str(reflection.get("title") or ""),
                "created_at": str(reflection.get("created_at") or ""),
            }
            parents[syn_id] = ""
            for link in reflection.get("materialized_experiments") or []:
                edges.append({"src": syn_id, "dst": str(link.get("experiment_id")), "kind": "derived"})
            for link in reflection.get("materialized_claims") or []:
                edges.append({"src": syn_id, "dst": str(link.get("claim_id")), "kind": "derived"})

        for resource in resources:
            res_id = str(resource["id"])
            path = str(resource.get("path") or "")
            entities[res_id] = {
                "id": res_id,
                "type": "resource",
                "label": str(resource.get("title") or path.rsplit("/", 1)[-1] or res_id),
                "status": "missing" if resource.get("missing") else str(resource.get("kind") or ""),
                "kind": str(resource.get("kind") or ""),
                "text": path,
                "created_at": str(resource.get("created_at") or ""),
            }
            # Parent = the first association whose target is on the board,
            # experiments preferred (spec parent order); the hydrated list is
            # already deterministically ordered (target_type, role, attempt).
            associations = sorted(
                resource.get("associations") or [],
                key=lambda a: str(a.get("target_type")) != "experiment",
            )
            parent = next(
                (str(a.get("target_id")) for a in associations if str(a.get("target_id")) in entities),
                "",
            )
            parents[res_id] = parent
            if parent:
                edges.append({"src": res_id, "dst": parent, "kind": "produced"})

        for review in reviews:
            rev_id = str(review["id"])
            target_id = str(review.get("target_id") or "")
            entities[rev_id] = {
                "id": rev_id,
                "type": "review",
                "label": f"{review.get('role') or 'review'} · {review.get('verdict') or ''}",
                "status": str(review.get("verdict") or ""),
                "verdict": str(review.get("verdict") or ""),
                "role": str(review.get("role") or ""),
                "text": str(review.get("synopsis") or review.get("notes") or ""),
                "created_at": str(review.get("created_at") or ""),
            }
            parents[rev_id] = target_id if target_id in entities else ""
            if target_id in entities:
                edges.append({"src": rev_id, "dst": target_id, "kind": "reviewed"})

        for entity_id, entity in entities.items():
            entity["last_touched_at"] = last_touched.get(entity_id, entity["created_at"])
        return {"entities": entities, "parents": parents, "edges": edges}

    # -- deterministic append-mostly placement --------------------------------

    def sync(self, *, project_id: str) -> dict[str, Any]:
        """Place every entity that lacks a position; never move a placed one."""
        collected = self._collect(project_id=project_id)
        entities, parents = collected["entities"], collected["parents"]
        with self.store.transaction() as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            rows = rows_to_dicts(rows=conn.execute(
                "SELECT * FROM map_layout WHERE project_id = ? ORDER BY placed_seq",
                (project_id,),
            ).fetchall())
            placed = {str(row["entity_id"]): row for row in rows}
            occupied = [
                (float(r["x"]), float(r["y"]), str(r["entity_type"]), str(r["entity_id"]))
                for r in rows
            ]
            root_count = sum(
                1 for r in rows
                if not r["parent_id"] and r["entity_type"] not in ("reflection", "resource")
            )
            reflection_count = sum(1 for r in rows if r["entity_type"] == "reflection")
            shelf_count = sum(
                1 for r in rows if not r["parent_id"] and r["entity_type"] == "resource"
            )
            next_seq = max((int(r["placed_seq"]) for r in rows), default=0) + 1
            missing = sorted(
                (e for eid, e in entities.items() if eid not in placed),
                key=lambda e: (e["created_at"], e["id"]),
            )
            for entity in missing:
                parent_id = parents.get(entity["id"], "")
                parent_row = placed.get(parent_id)
                if entity["type"] == "reflection":
                    x, y = self._place_on_reflection_ring(entity, reflection_count, occupied)
                    parent_id = ""
                    reflection_count += 1
                elif parent_row is None and entity["type"] == "resource":
                    x, y = self._place_on_shelf(entity, shelf_count, occupied)
                    parent_id = ""
                    shelf_count += 1
                elif parent_row is None:
                    x, y = self._place_root(entity, root_count, occupied)
                    parent_id = ""
                    root_count += 1
                else:
                    x, y = self._place_child(
                        entity,
                        (float(parent_row["x"]), float(parent_row["y"]), str(parent_row["entity_type"]), parent_id),
                        occupied,
                    )
                row = {
                    "entity_id": entity["id"],
                    "entity_type": entity["type"],
                    "x": x,
                    "y": y,
                    "pinned": 0,
                    "parent_id": parent_id,
                    "placed_seq": next_seq,
                }
                conn.execute(
                    """
                    INSERT INTO map_layout
                      (project_id, entity_id, entity_type, x, y, pinned, parent_id, placed_seq, created_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                    ON CONFLICT (project_id, entity_id) DO NOTHING
                    """,
                    (project_id, entity["id"], entity["type"], x, y, parent_id, next_seq, now_iso()),
                )
                placed[entity["id"]] = row
                occupied.append((x, y, entity["type"], entity["id"]))
                next_seq += 1
        collected["layout"] = placed
        return collected

    def _is_free(
        self,
        x: float,
        y: float,
        kind: str,
        occupied: list[tuple[float, float, str, str]],
        exempt_id: str = "",
    ) -> bool:
        for ox, oy, okind, oid in occupied:
            if oid == exempt_id:
                continue
            if math.hypot(x - ox, y - oy) < _sep(kind, okind):
                return False
        return True

    def _place_root(
        self, entity: dict[str, Any], index: int, occupied: list[tuple[float, float, str, str]]
    ) -> tuple[float, float]:
        k = index
        while True:
            r = ROOT_SPIRAL_C * math.sqrt(k)
            angle = k * GOLDEN_ANGLE
            x, y = r * math.cos(angle), r * math.sin(angle)
            if self._is_free(x, y, entity["type"], occupied):
                return x, y
            k += 1

    def _place_on_reflection_ring(
        self, entity: dict[str, Any], index: int, occupied: list[tuple[float, float, str, str]]
    ) -> tuple[float, float]:
        """Reflections are project-level: a dedicated ring around the origin."""
        k = index
        while True:
            ring, slot = divmod(k, 8)
            r = REFLECTION_RING_R + ring * REFLECTION_RING_STEP
            angle = _jitter(entity["id"]) + slot * math.tau / 8
            x, y = r * math.cos(angle), r * math.sin(angle)
            if self._is_free(x, y, entity["type"], occupied):
                return x, y
            k += 1

    def _place_on_shelf(
        self, entity: dict[str, Any], index: int, occupied: list[tuple[float, float, str, str]]
    ) -> tuple[float, float]:
        """Orphan resources tile a compact library grid off the origin — deep-
        zoom material, never research territory that seeds a region."""
        k = index
        while True:
            row, col = divmod(k, SHELF_COLS)
            x = SHELF_ORIGIN[0] + col * SHELF_STEP
            y = SHELF_ORIGIN[1] - row * SHELF_STEP
            if self._is_free(x, y, entity["type"], occupied):
                return x, y
            k += 1

    def _place_child(
        self,
        entity: dict[str, Any],
        parent: tuple[float, float, str, str],
        occupied: list[tuple[float, float, str, str]],
    ) -> tuple[float, float]:
        """Nearest free golden-ratio slot around the parent; the id-seeded
        angle jitter keeps arcs organic-looking but reproducible."""
        px, py, _pkind, pid = parent
        base = CHILD_RING_BASE.get(entity["type"], CHILD_RING_DEFAULT)
        jitter = _jitter(entity["id"])
        for ring in range(64):
            r = base + ring * CHILD_RING_STEP
            slots = max(6, int(math.tau * r / DEFAULT_SEP))
            for slot in range(slots):
                angle = jitter + slot * math.tau / slots
                x, y = px + r * math.cos(angle), py + r * math.sin(angle)
                if self._is_free(x, y, entity["type"], occupied, exempt_id=pid):
                    return x, y
        # Unreachable at plausible board sizes; fall back to a far ring slot.
        r = base + 64 * CHILD_RING_STEP
        return px + r * math.cos(jitter), py + r * math.sin(jitter)

    # -- versions and views ----------------------------------------------------

    def layout_version(self, *, project_id: str) -> str:
        conn = self.store.connect()
        try:
            return self._layout_version(conn=conn, project_id=project_id)
        finally:
            conn.close()

    def _layout_version(self, *, conn: Any, project_id: str) -> str:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(placed_seq), 0) AS max_seq FROM map_layout WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        pins = conn.execute(
            "SELECT entity_id, x, y FROM map_layout WHERE project_id = ? AND pinned = 1 ORDER BY entity_id",
            (project_id,),
        ).fetchall()
        pin_fp = hashlib.sha1(
            "|".join(f"{p['entity_id']}:{float(p['x']):.1f}:{float(p['y']):.1f}" for p in pins).encode("utf-8")
        ).hexdigest()[:8]
        return hashlib.sha1(
            f"{int(row['n'])}:{int(row['max_seq'])}:{pin_fp}".encode("utf-8")
        ).hexdigest()[:12]

    def _layout_rows(self, *, project_id: str) -> list[dict[str, Any]]:
        conn = self.store.connect()
        try:
            return rows_to_dicts(rows=conn.execute(
                "SELECT * FROM map_layout WHERE project_id = ? ORDER BY placed_seq",
                (project_id,),
            ).fetchall())
        finally:
            conn.close()

    def _render_entities(self, collected: dict[str, Any]) -> list[dict[str, Any]]:
        """Entities merged with their frozen positions + lineage-derived
        display hints. Layout rows whose entity vanished are kept in the table
        (append-only) but not rendered."""
        layout = collected["layout"]
        entities = collected["entities"]
        children: dict[str, int] = {}
        for row in layout.values():
            parent = str(row.get("parent_id") or "")
            if parent:
                children[parent] = children.get(parent, 0) + 1

        def region_root(entity_id: str) -> str:
            seen = set()
            current = entity_id
            while current and current not in seen:
                seen.add(current)
                parent = str(layout.get(current, {}).get("parent_id") or "")
                if not parent:
                    return current
                current = parent
            return current

        merged = []
        for entity_id, row in layout.items():
            entity = entities.get(entity_id)
            if entity is None:
                continue
            merged.append({
                **entity,
                "x": float(row["x"]),
                "y": float(row["y"]),
                "pinned": bool(row["pinned"]),
                "is_root": not row.get("parent_id"),
                "children_count": children.get(entity_id, 0),
                "region_root": region_root(entity_id),
            })
        return merged

    def state(self, *, project_id: str) -> dict[str, Any]:
        """Positions/bounds JSON for UI hit-testing and drag ONLY — never an
        agent surface (the hard line: agents perceive rendered pixels)."""
        collected = self.sync(project_id=project_id)
        rendered = self._render_entities(collected)
        # Fit bounds frame the L0 territories (not the off-canvas resource
        # shelf), matching the server overview; the UI's initial fit + Fit
        # button use these so small projects aren't stranded in a corner.
        overview = [e for e in rendered if e["type"] in ("claim", "experiment")] or rendered
        bounds = self._bounds(overview)
        return {
            "layout_version": self.layout_version(project_id=project_id),
            "entities": [
                {
                    "id": e["id"],
                    "type": e["type"],
                    "x": e["x"],
                    "y": e["y"],
                    "pinned": e["pinned"],
                    "label": e["label"],
                    "status": e.get("status") or "",
                    "last_touched_at": e.get("last_touched_at") or "",
                }
                for e in rendered
            ],
            "bounds": bounds,
            "cell": CELL,
            "registers": [[name, threshold] for name, threshold in REGISTERS],
        }

    def _bounds(self, rendered: list[dict[str, Any]]) -> dict[str, float]:
        if not rendered:
            return {"min_x": -CELL, "min_y": -CELL, "max_x": CELL, "max_y": CELL}
        xs = [e["x"] for e in rendered]
        ys = [e["y"] for e in rendered]
        return {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)}

    # -- rendering -------------------------------------------------------------

    def snapshot(
        self,
        *,
        project_id: str,
        cx: float | None = None,
        cy: float | None = None,
        zoom: float | None = None,
        cell: str | None = None,
        w: int = DEFAULT_SNAPSHOT_SIZE[0],
        h: int = DEFAULT_SNAPSHOT_SIZE[1],
        scale: float = 1.0,
        now: datetime | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        """Render one viewport to PNG. No viewport = fit-all overview."""
        w = max(MIN_SIZE, min(MAX_SIZE, int(w)))
        h = max(MIN_SIZE, min(MAX_SIZE, int(h)))
        scale = max(1.0, min(3.0, float(scale)))
        collected = self.sync(project_id=project_id)
        rendered = self._render_entities(collected)
        if cell:
            try:
                cx, cy = parse_cell(cell)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            # Cell fills the short axis: at the default 1200x800 that is
            # zoom 2.0 — exactly the L3 threshold, ids legible.
            zoom = zoom or min(w, h) / CELL / scale
        if cx is None or cy is None or zoom is None:
            cx, cy, zoom = self._fit_viewport(rendered, w / scale, h / scale)
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, float(zoom)))
        viewport = Viewport(cx=float(cx), cy=float(cy), zoom=zoom, w=round(w * scale), h=round(h * scale), scale=scale)
        now = now or datetime.now(timezone.utc)
        meta = {
            "viewport": {"cx": viewport.cx, "cy": viewport.cy, "zoom": zoom, "w": w, "h": h},
            "register": register_for_zoom(zoom),
            "layout_version": self.layout_version(project_id=project_id),
            "entity_count": len(rendered),
        }
        key = self._cache_key(project_id=project_id, meta=meta, scale=scale, now=now)
        cached = self._render_cache.get(key)
        if cached is not None:
            self._render_cache.move_to_end(key)
            return cached
        scene = build_scene(entities=rendered, edges=collected["edges"], viewport=viewport, now=now)
        png = rasterize(scene, viewport.w, viewport.h)
        self._render_cache[key] = (png, meta)
        while len(self._render_cache) > 48:
            self._render_cache.popitem(last=False)
        return png, meta

    def _cache_key(self, *, project_id: str, meta: dict[str, Any], scale: float, now: datetime) -> tuple:
        # The spec keys the cache on (layout_version, viewport, size); entity
        # STATE also colors the render, so the event signal joins the key, and
        # `now` is bucketed to 10 minutes so freshness glow still decays.
        vp = meta["viewport"]
        return (
            project_id,
            meta["layout_version"],
            self.store.project_event_signal(project_id=project_id),
            round(vp["cx"], 1),
            round(vp["cy"], 1),
            round(vp["zoom"], 4),
            vp["w"],
            vp["h"],
            scale,
            int(now.timestamp() // 600),
        )

    def _fit_viewport(
        self, rendered: list[dict[str, Any]], w: float, h: float
    ) -> tuple[float, float, float]:
        # Frame on the L0 vocabulary (claim/experiment territories) only — the
        # off-canvas orphan-resource shelf and wide resource halos must not drag
        # the overview so a small project doesn't render tiny in a corner.
        overview = [e for e in rendered if e["type"] in ("claim", "experiment")] or rendered
        bounds = self._bounds(overview)
        pad = 320.0
        span_x = bounds["max_x"] - bounds["min_x"] + pad * 2
        span_y = bounds["max_y"] - bounds["min_y"] + pad * 2
        zoom = min(w / span_x, h / span_y)
        cx = (bounds["min_x"] + bounds["max_x"]) / 2
        cy = (bounds["min_y"] + bounds["max_y"]) / 2
        return cx, cy, max(ZOOM_MIN, min(ZOOM_MAX, zoom))

    def locate(
        self,
        *,
        project_id: str,
        entity_id: str,
        zoom: float = LOCATE_ZOOM,
        w: int = DEFAULT_SNAPSHOT_SIZE[0],
        h: int = DEFAULT_SNAPSHOT_SIZE[1],
        now: datetime | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        self.sync(project_id=project_id)
        rows = self._layout_rows(project_id=project_id)
        row = next((r for r in rows if r["entity_id"] == entity_id), None)
        if row is None:
            raise NotFoundError(f"entity not on the map: {entity_id}")
        return self.snapshot(
            project_id=project_id,
            cx=float(row["x"]),
            cy=float(row["y"]),
            zoom=zoom,
            w=w,
            h=h,
            now=now,
        )

    # -- pins (human authoring; UI-only surface) -------------------------------

    def pin(self, *, project_id: str, entity_id: str, x: float, y: float) -> dict[str, Any]:
        self.sync(project_id=project_id)
        with self.store.transaction() as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            cursor = conn.execute(
                "UPDATE map_layout SET pinned = 1, x = ?, y = ? WHERE project_id = ? AND entity_id = ?",
                (float(x), float(y), project_id, entity_id),
            )
            if getattr(cursor, "rowcount", 0) == 0:
                raise NotFoundError(f"entity not on the map: {entity_id}")
            self.store.record_event(
                conn=conn,
                project_id=project_id,
                event_type="map.entity_pinned",
                target_type="map",
                target_id=entity_id,
                payload={"x": float(x), "y": float(y)},
            )
            return {"ok": True, "layout_version": self._layout_version(conn=conn, project_id=project_id)}

    def unpin(self, *, project_id: str, entity_id: str) -> dict[str, Any]:
        """Position stays (unpin restores nothing); it just stops being authored."""
        with self.store.transaction() as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            cursor = conn.execute(
                "UPDATE map_layout SET pinned = 0 WHERE project_id = ? AND entity_id = ?",
                (project_id, entity_id),
            )
            if getattr(cursor, "rowcount", 0) == 0:
                raise NotFoundError(f"entity not on the map: {entity_id}")
            return {"ok": True, "layout_version": self._layout_version(conn=conn, project_id=project_id)}

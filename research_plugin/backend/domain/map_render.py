"""Research Map renderer — one deterministic rasterizer for humans and agents.

Pixel parity is the feature's hard line (dev_docs/research_plugin/
RESEARCH_MAP_V1.md): the UI viewport and the agent snapshot tools must show
exactly the same pixels, so there is exactly ONE renderer and it is a pure
function of (entities, edges, viewport, now). No wall clock, no randomness, no
AI in the render path; the bundled font pins text metrics.

Split into ``build_scene`` (which decides WHAT is drawn per zoom register —
the cartographic vocabulary) and ``rasterize`` (which turns scene ops into
pixels), so per-register content assertions can run on the scene without
depending on font rasterization.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils import parse_iso

FONT_PATH = Path(__file__).parent / "fonts" / "DejaVuSans.ttf"

# Zoom registers (Papyrus lesson: each band is an editorial design, not a
# scale factor). zoom = screen pixels per world unit; a register applies from
# its threshold up to the next one. The single thresholds table.
REGISTERS: tuple[tuple[str, float], ...] = (
    ("L0", 0.0),
    ("L1", 0.32),
    ("L2", 0.85),
    ("L3", 2.0),
)

# World-anchored A1-style cells. Refs are absolute (column H / row 8 straddle
# the world origin) so "zoom into C4" names the same place in every snapshot.
CELL = 400.0
COL_OFFSET = 7
ROW_OFFSET = 7

# Time traces on the living board: the freshness GLOW is a tight rim-light on
# genuinely-recent work (a sharp few-day window); the slower FRESH_DECAY_DAYS
# governs color warmth, and untouched entities desaturate past STALE_AFTER_DAYS.
GLOW_WINDOW_DAYS = 3.0
FRESH_DECAY_DAYS = 7.0
STALE_AFTER_DAYS = 30.0

BG = (250, 249, 246)
GRID = (206, 201, 191)
INK = (51, 49, 46)
# Ids are the agent's action bridge — near-black for AA contrast on any card
# fill, never the muddy olive that failed contrast on tan resource cards.
INK_ID = (38, 36, 32)
MUTED = (122, 118, 110)
FAINT = (176, 171, 161)
CARD_BG = (255, 255, 254)
# Freshness bloom: a cool, bright rim-light, deliberately OFF the warm entity
# palette so "recent" separates from the tan/green fills instead of smearing
# into them.
GLOW = (120, 205, 240)
PIN = (168, 46, 37)

EXPERIMENT_STATUS_COLORS = {
    "planned": (150, 144, 134),
    "design_review": (196, 148, 58),
    "ready_to_run": (109, 140, 190),
    "provisioning": (98, 148, 222),
    "running": (31, 111, 235),
    "experiment_review": (176, 128, 48),
    "complete": (47, 110, 53),
    "failed": (168, 46, 37),
    "abandoned": (154, 149, 140),
}
CLAIM_STATUS_COLORS = {
    "draft": (150, 144, 134),
    "active": (86, 108, 150),
    "supported": (47, 110, 53),
    "weakened": (217, 130, 43),
    "contradicted": (168, 46, 37),
    "abandoned": (154, 149, 140),
}
VERDICT_COLORS = {
    "pass": (47, 110, 53),
    "needs_changes": (217, 130, 43),
    "fail": (168, 46, 37),
}
REFLECTION_STATUS_COLORS = {
    "reflecting": (146, 122, 180),
    "synthesizing": (146, 122, 180),
    "synthesis_review": (176, 128, 48),
    "published": (104, 76, 150),
    "abandoned": (154, 149, 140),
}
RESOURCE_COLOR = (146, 124, 92)
CONFIDENCE_DESAT = {"low": 0.55, "medium": 0.25, "high": 0.0}
# Confidence rendered as a colored strength pill (color = state, per the spec)
# rather than a floating gray word.
CONFIDENCE_PILL = {"low": (176, 128, 48), "medium": (109, 140, 190), "high": (42, 122, 56)}

EDGE_STYLES = {
    # kind -> (rgb, width, dash, arrow)
    "tests": ((110, 105, 96), 2, None, True),
    "refutes": ((176, 84, 62), 2, (7, 5), True),
    "produced": ((165, 160, 150), 1, None, False),
    "derived": ((165, 160, 150), 1, (4, 5), False),
    "reviewed": ((150, 144, 134), 1, (2, 5), False),
}


@dataclass(frozen=True)
class Viewport:
    """World-space camera: ``zoom`` is the SEMANTIC px-per-world-unit (drives
    the register); ``scale`` supersamples for hi-dpi screens without changing
    the register or layout, so human and agent still see the same content."""

    cx: float
    cy: float
    zoom: float
    w: int
    h: int
    scale: float = 1.0

    def to_px(self, x: float, y: float) -> tuple[float, float]:
        z = self.zoom * self.scale
        return ((x - self.cx) * z + self.w / 2, (y - self.cy) * z + self.h / 2)


def register_for_zoom(zoom: float) -> str:
    name = REGISTERS[0][0]
    for candidate, threshold in REGISTERS:
        if zoom >= threshold:
            name = candidate
    return name


def _col_letters(index: int) -> str:
    letters = ""
    index += 1  # excel-style: 1 -> A
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def cell_label(x: float, y: float) -> str | None:
    col = COL_OFFSET + math.floor(x / CELL)
    row = ROW_OFFSET + math.floor(y / CELL) + 1
    if col < 0 or row < 1:
        return None
    return f"{_col_letters(col)}{row}"


def parse_cell(ref: str) -> tuple[float, float]:
    """Center of a world-anchored cell ref like ``C4``. Raises ValueError."""
    ref = (ref or "").strip().upper()
    letters = ref.rstrip("0123456789")
    digits = ref[len(letters):]
    if not letters or not digits or not letters.isalpha():
        raise ValueError(f"invalid cell ref: {ref!r}")
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - ord("A") + 1)
    col -= 1
    row = int(digits)
    x = (col - COL_OFFSET) * CELL + CELL / 2
    y = (row - 1 - ROW_OFFSET) * CELL + CELL / 2
    return x, y


def _age_days(entity: dict[str, Any], now: datetime) -> float:
    touched = parse_iso(entity.get("last_touched_at") or entity.get("created_at"))
    if touched is None:
        return STALE_AFTER_DAYS
    if touched.tzinfo is None:
        touched = touched.replace(tzinfo=timezone.utc)
    return max(0.0, (now - touched).total_seconds() / 86400.0)


def _blend(color: tuple[int, int, int], other: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(c + (o - c) * t) for c, o in zip(color, other))


def entity_color(entity: dict[str, Any], age_days: float) -> tuple[int, int, int]:
    kind = entity.get("type")
    if kind == "experiment":
        color = EXPERIMENT_STATUS_COLORS.get(str(entity.get("status")), FAINT)
    elif kind == "claim":
        color = CLAIM_STATUS_COLORS.get(str(entity.get("status")), FAINT)
        color = _blend(color, FAINT, CONFIDENCE_DESAT.get(str(entity.get("confidence")), 0.25))
    elif kind == "review":
        color = VERDICT_COLORS.get(str(entity.get("verdict")), FAINT)
    elif kind == "reflection":
        color = REFLECTION_STATUS_COLORS.get(str(entity.get("status")), (124, 96, 160))
    else:
        color = RESOURCE_COLOR
    if age_days > STALE_AFTER_DAYS:
        color = _blend(color, BG, min(0.65, (age_days - STALE_AFTER_DAYS) / 45.0))
    return color


def _fit(text: str, max_chars: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip() + "…"


def _strip_common_prefix(texts: list[str]) -> dict[str, str]:
    """Map each label to itself minus the boilerplate word-prefix shared across
    the set. Real projects mint incremental claims that all start
    'From the ce2ebe9 incumbent, …'; stripping that frees the whole truncation
    budget for the distinguishing tail (design-review consensus)."""
    if len(texts) < 2:
        return {t: t for t in texts}
    word_lists = [t.split(" ") for t in texts]
    common = 0
    for words in zip(*word_lists):
        if len(set(words)) == 1 and common < min(len(w) for w in word_lists) - 1:
            common += 1
        else:
            break
    if common == 0:
        return {t: t for t in texts}
    out: dict[str, str] = {}
    for text, words in zip(texts, word_lists):
        tail = " ".join(words[common:]).lstrip(" ,;:-—")
        out[text] = (tail[:1].upper() + tail[1:]) if tail else text
    return out


def _wrap_or_fit(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Wrap to at most ``max_lines``; the final line ellipsizes if still over."""
    return _wrap(text, max_chars, max_lines)


def _wrap(text: str, max_chars: int, max_lines: int) -> list[str]:
    words = " ".join(str(text or "").split()).split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if not word:
            continue
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(words)) > sum(len(l) for l in lines):
        lines[-1] = _fit(lines[-1] + "…", max_chars)
    # Hard cap: a single word longer than max_chars can't be broken, so ellipsize
    # it — otherwise long hyphenated names (exp titles) overflow their card.
    return [_fit(line, max_chars) for line in lines]


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _inflate(hull: list[tuple[float, float]], pad: float) -> list[tuple[float, float]]:
    cx = sum(p[0] for p in hull) / len(hull)
    cy = sum(p[1] for p in hull) / len(hull)
    out = []
    for x, y in hull:
        d = math.hypot(x - cx, y - cy) or 1.0
        out.append((x + (x - cx) / d * pad, y + (y - cy) / d * pad))
    return out


# ---- scene building ---------------------------------------------------------


# Cartographic registers: each band draws a different VOCABULARY, not just a
# different scale (Papyrus lesson). Resources are evidence attached to
# experiments — deep-zoom material — so they stay off the board until L2, where
# the reader has chosen to inspect one cluster. This is what keeps L0/L1
# scannable on real projects (tens of experiments, hundreds of resources).
REGISTER_TYPES = {
    "L0": frozenset({"claim", "experiment"}),
    "L1": frozenset({"claim", "experiment", "review", "reflection"}),
    "L2": frozenset({"claim", "experiment", "review", "reflection", "resource"}),
    "L3": frozenset({"claim", "experiment", "review", "reflection", "resource"}),
}
# Edges by register: L0 relies on region hulls for grouping (no line clutter);
# lineage arrows appear at L1; the produced/derived provenance mesh only at L2+
# so a reflection's fan-out never hairballs the overview.
EDGE_REGISTERS = {
    "L0": frozenset(),
    "L1": frozenset({"tests", "refutes", "reviewed"}),
    "L2": frozenset({"tests", "refutes", "reviewed", "produced", "derived"}),
    "L3": frozenset({"tests", "refutes", "reviewed", "produced", "derived"}),
}


def build_scene(
    *,
    entities: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    viewport: Viewport,
    now: datetime,
) -> list[dict[str, Any]]:
    """Ordered draw ops for one viewport. Entities carry map positions plus
    the display fields assembled by the map service."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    register = register_for_zoom(viewport.zoom)
    shown_types = REGISTER_TYPES[register]
    shown_edges = EDGE_REGISTERS[register]
    s = viewport.scale
    ops: list[dict[str, Any]] = []

    pad_px = 320 * s
    visible: dict[str, dict[str, Any]] = {}
    positions: dict[str, tuple[float, float]] = {}
    for entity in entities:
        px, py = viewport.to_px(float(entity["x"]), float(entity["y"]))
        positions[str(entity["id"])] = (px, py)
        if entity["type"] not in shown_types:
            continue
        if -pad_px <= px <= viewport.w + pad_px and -pad_px <= py <= viewport.h + pad_px:
            visible[str(entity["id"])] = entity

    ops.extend(_grid_ops(viewport, register))
    if register == "L0":
        ops.extend(_region_ops(entities, positions, viewport, now))
    if shown_edges:
        ops.extend(_edge_ops(edges, positions, register, s, shown_edges, viewport.w, viewport.h))
    if register != "L0":
        ops.extend(_glow_ops(visible, positions, register, now, s))
    # Reviews dock onto their target, so draw them under the entity cards.
    order = sorted(visible.items(), key=lambda kv: kv[1]["type"] != "review")
    l1_labels: list[dict[str, Any]] = []
    for entity_id, entity in order:
        px, py = positions[entity_id]
        age = _age_days(entity, now)
        color = entity_color(entity, age)
        if register == "L0":
            r = 6.5 * s if entity["type"] == "claim" else 5.5 * s
            ops.append({"op": "ellipse", "xy": (px - r, py - r, px + r, py + r),
                        "fill": (*color, 255), "outline": (*_blend(color, INK, 0.4), 255),
                        "width": max(1, round(1.4 * s))})
        elif register == "L1":
            ops.extend(_l1_ops(entity, px, py, color, s))
            if entity["type"] in ("claim", "experiment"):
                l1_labels.append({
                    "priority": _l1_priority(entity),
                    "text": _fit(str(entity.get("label") or entity.get("id")), 30),
                    "x": px, "y": py + 15 * s + 12 * s,
                    "size": round(11 * s), "color": INK, "anchor": "ms", "bold": False,
                })
        else:
            ops.extend(_card_ops(entity, px, py, color, s, register))
    if l1_labels:
        ops.extend(_place_labels(l1_labels, s, cap=28))
    ops.extend(_margin_ops(viewport, register))
    ops.extend(_legend_ops(viewport, register))
    return ops


def _legend_ops(viewport: Viewport, register: str) -> list[dict[str, Any]]:
    """In-frame key so a snapshot is self-describing for the agent audience —
    color/shape semantics must be recoverable from the pixels alone."""
    s = viewport.scale
    if register == "L0":
        rows = [
            ("health", REGION_ALIVE, "running"),
            ("health", REGION_WON, "complete"),
            ("health", REGION_MIXED, "mixed"),
            ("health", REGION_LOST, "failed"),
            ("health", REGION_IDLE, "idle / stale"),
        ]
    else:
        rows = [("kind", None, k) for k in ("claim", "experiment", "review", "resource", "reflection")]
    size = round(9.5 * s)
    row_h = round(15 * s)
    sw = round(11 * s)
    pad = round(9 * s)
    width = round(128 * s)
    height = pad * 2 + row_h * len(rows)
    x0 = round(10 * s)
    y0 = viewport.h - height - round(24 * s)
    ops: list[dict[str, Any]] = [{
        "op": "rect", "xy": (x0, y0, x0 + width, y0 + height),
        "fill": (*BG, 232), "outline": (*GRID, 150), "width": max(1, round(s)),
        "radius": round(6 * s),
    }]
    for i, (mode, color, text) in enumerate(rows):
        cy = y0 + pad + row_h * i + row_h // 2
        cx = x0 + pad + sw // 2
        if mode == "health":
            ops.append({"op": "ellipse", "xy": (cx - sw / 2, cy - sw / 2, cx + sw / 2, cy + sw / 2),
                        "fill": (*color, 210), "outline": (*color, 255), "width": max(1, round(s))})
        else:
            ops.extend(_glyph_ops({"type": text}, cx, cy, MUTED, sw * 0.5))
        ops.append({"op": "text", "xy": (x0 + pad + sw + round(7 * s), cy), "text": text,
                    "size": size, "color": MUTED, "anchor": "lm"})
    return ops


def _grid_ops(viewport: Viewport, register: str) -> list[dict[str, Any]]:
    """A world-anchored addressing lattice, kept quiet so it never competes with
    the content — faintest at L0 (the squint register), a touch firmer deeper in
    where it serves as a ruler."""
    ops: list[dict[str, Any]] = []
    z = viewport.zoom * viewport.scale
    heavy_a, light_a = (18, 9) if register == "L0" else (40, 20)
    world_left = viewport.cx - viewport.w / 2 / z
    world_top = viewport.cy - viewport.h / 2 / z
    step = CELL if register in ("L0", "L1") else CELL / 4
    x = math.floor(world_left / step) * step
    while (px := viewport.to_px(x, 0)[0]) <= viewport.w:
        heavy = abs(x / CELL - round(x / CELL)) < 1e-9
        ops.append({"op": "line", "pts": [(px, 0), (px, viewport.h)], "color": (*GRID, heavy_a if heavy else light_a), "width": 1})
        x += step
    y = math.floor(world_top / step) * step
    while (py := viewport.to_px(0, y)[1]) <= viewport.h:
        heavy = abs(y / CELL - round(y / CELL)) < 1e-9
        ops.append({"op": "line", "pts": [(0, py), (viewport.w, py)], "color": (*GRID, heavy_a if heavy else light_a), "width": 1})
        y += step
    return ops


# Region health = the answer to "where is this project alive vs dead?" — a
# continuous heat-map from the win/loss balance carried in the FILL itself, so
# the field shows range at a squint without reading a single dot or label.
REGION_ALIVE = (34, 116, 226)     # something running — advances
REGION_WON = (44, 132, 62)        # all complete — healthy
REGION_MIXED = (206, 146, 40)     # contested — some wins, some losses
REGION_LOST = (182, 54, 46)       # failures dominate — dead end
REGION_IDLE = (170, 166, 156)     # planned / no verdict yet — recedes
MAX_REGION_LABELS = 12


def _health_ramp(score: float) -> tuple[int, int, int]:
    """score in [-1, 1]: +1 all-complete (green) → 0 contested (amber) → -1
    failures dominate (red). A smooth ramp so partial failure reads as a hue
    shift, not a binary flip."""
    if score >= 0:
        return _blend(REGION_MIXED, REGION_WON, score)
    return _blend(REGION_MIXED, REGION_LOST, -score)


def _region_health(members: list[dict[str, Any]], now: datetime) -> tuple[tuple[int, int, int], float, int]:
    """(tint, live_weight 0..1, experiment_count) for a claim cluster. Tint is a
    continuous win/loss heat-map; live weight drives fill opacity so decided,
    active territory advances and idle/stale territory recedes."""
    won = lost = alive = 0
    ages: list[float] = []
    for m in members:
        ages.append(_age_days(m, now))
        if m["type"] != "experiment":
            continue
        status = str(m.get("status"))
        if status in ("running", "provisioning"):
            alive += 1
        elif status == "complete":
            won += 1
        elif status in ("failed", "abandoned"):
            lost += 1
    decided = won + lost
    exp_count = decided + alive
    mean_age = sum(ages) / len(ages) if ages else 0.0
    if alive:
        tint, live = REGION_ALIVE, 1.0
    elif decided:
        tint = _health_ramp((won - lost) / decided)
        live = 0.9
    else:
        tint, live = REGION_IDLE, 0.32
    if mean_age > STALE_AFTER_DAYS:
        fade = min(0.7, (mean_age - STALE_AFTER_DAYS) / 60.0)
        tint = _blend(tint, REGION_IDLE, fade)
        live *= (1.0 - 0.5 * fade)
    return tint, live, exp_count


def _region_ops(
    entities: list[dict[str, Any]],
    positions: dict[str, tuple[float, float]],
    viewport: Viewport,
    now: datetime,
) -> list[dict[str, Any]]:
    """L0 program shape: one soft hull per claim territory (claim + its
    experiments). Fill opacity tracks health so live threads advance and idle
    ones recede; territory size tracks experiment count; labels are prefix-
    stripped, deduped, and capped so the overview reads by color and shape, not
    prose (founder's 'zero reading' line)."""
    shown = REGISTER_TYPES["L0"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        if entity.get("type") not in shown:
            continue
        root = str(entity.get("region_root") or "")
        if root:
            groups.setdefault(root, []).append(entity)
    roots = {str(e["id"]): e for e in entities}
    s = viewport.scale
    ops: list[dict[str, Any]] = []
    label_seeds: list[tuple[int, float, float, tuple[int, int, int], str]] = []
    base_pad = 34 * s + 22 * viewport.zoom * s
    for root_id, members in sorted(groups.items()):
        pts = [positions[str(m["id"])] for m in members]
        tint, live, exp_count = _region_health(members, now)
        fill_a = round(46 + 66 * live)      # 46..112 — territory reads as colored, not pale
        line_a = round(110 + 90 * live)     # 110..200
        pad = base_pad + min(26 * s, 4.5 * s * exp_count)  # bigger thread = bigger territory
        if len(pts) <= 2:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            spread = 0 if len(pts) == 1 else math.hypot(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1]) / 2
            r = pad + spread
            ops.append({"op": "ellipse", "xy": (cx - r, cy - r, cx + r, cy + r),
                        "fill": (*tint, fill_a), "outline": (*tint, line_a), "width": max(1, round(1.4 * s))})
            lx, ly = cx, cy - r
        else:
            hull = _inflate(_convex_hull(pts), pad)
            ops.append({"op": "poly", "pts": hull, "fill": (*tint, fill_a),
                        "outline": (*tint, line_a), "width": max(1, round(1.4 * s))})
            lx = sum(p[0] for p in hull) / len(hull)
            ly = min(p[1] for p in hull)
        root = roots.get(root_id)
        if root is not None:
            label_seeds.append((len(members), lx, ly - 7 * s, _blend(tint, INK, 0.5), str(root.get("label") or root_id)))
    # Strip the boilerplate prefix shared across territory names before fitting.
    stripped = _strip_common_prefix([seed[4] for seed in label_seeds])
    min_y = round(30 * s)  # keep labels below the top coordinate ruler band
    labels = [
        {"priority": prio, "x": lx, "y": max(ly, min_y), "color": color, "size": round(12.5 * s),
         "text": _fit(stripped.get(text, text), 26)}
        for prio, lx, ly, color, text in label_seeds
    ]
    ops.extend(_place_labels(labels, s, cap=MAX_REGION_LABELS))
    return ops


def _place_labels(candidates: list[dict[str, Any]], s: float, cap: int) -> list[dict[str, Any]]:
    """Greedy non-overlapping label placement: biggest territories win the
    right to a label; anything that would collide or exceed the cap is dropped
    (the entity is still one zoom away). Prevents the pileup that made dense
    real projects unreadable."""
    placed_boxes: list[tuple[float, float, float, float]] = []
    seen_text: set[str] = set()
    ops: list[dict[str, Any]] = []
    for cand in sorted(candidates, key=lambda c: -c["priority"]):
        if len(placed_boxes) >= cap:
            break
        # Real projects repeat truncated statements (agent-authored incremental
        # claims); one label per distinct text keeps the overview honest.
        if cand["text"] in seen_text:
            continue
        half_w = len(cand["text"]) * cand["size"] * 0.30
        half_h = cand["size"] * 0.7
        box = (cand["x"] - half_w, cand["y"] - half_h, cand["x"] + half_w, cand["y"] + half_h)
        if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in placed_boxes):
            continue
        placed_boxes.append(box)
        seen_text.add(cand["text"])
        ops.append({
            "op": "text", "xy": (cand["x"], cand["y"]), "text": cand["text"],
            "size": cand["size"], "color": cand["color"],
            "bold": bool(cand.get("bold", True)), "anchor": cand.get("anchor", "ms"),
            "halo": True,
        })
    return ops


def _edge_ops(
    edges: list[dict[str, Any]],
    positions: dict[str, tuple[float, float]],
    register: str,
    s: float,
    shown: frozenset,
    w: int,
    h: int,
) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    provenance = {"produced", "derived"}
    margin = 120 * s

    def on_frame(p: tuple[float, float]) -> bool:
        return -margin <= p[0] <= w + margin and -margin <= p[1] <= h + margin

    for edge in edges:
        kind = str(edge.get("kind"))
        if kind not in shown:
            continue
        src = positions.get(str(edge.get("src")))
        dst = positions.get(str(edge.get("dst")))
        if src is None or dst is None:
            continue
        src_on, dst_on = on_frame(src), on_frame(dst)
        if not src_on and not dst_on:
            continue  # both endpoints off-frame: pure clutter
        # One endpoint off-frame: shorten to a stub toward it ("more that way")
        # rather than a long ray to nothing (design-review defect).
        if src_on and not dst_on:
            dst = _stub(src, dst, 46 * s)
        elif dst_on and not src_on:
            src = _stub(dst, src, 46 * s)
        color, width, dash, arrow = EDGE_STYLES.get(kind, EDGE_STYLES["produced"])
        alpha = 70 if kind in provenance else 150
        ops.append({
            "op": "edge",
            "frm": src,
            "to": dst,
            "color": (*color, alpha),
            "width": max(1, round(width * s)),
            "dash": tuple(round(d * s) for d in dash) if dash else None,
            "arrow": arrow and on_frame(dst),
        })
    return ops


def _stub(anchor: tuple[float, float], toward: tuple[float, float], length: float) -> tuple[float, float]:
    dx, dy = toward[0] - anchor[0], toward[1] - anchor[1]
    d = math.hypot(dx, dy) or 1.0
    return (anchor[0] + dx / d * length, anchor[1] + dy / d * length)


def _glow_ops(
    visible: dict[str, dict[str, Any]],
    positions: dict[str, tuple[float, float]],
    register: str,
    now: datetime,
    s: float,
) -> list[dict[str, Any]]:
    """Freshness as a tight rim-light on genuinely recent work only. Gated to a
    narrow window and a small radius so 'new' reads as signal, not a field of
    blur (design-review consensus: glow was firing on everything)."""
    radius = {"L1": 15.0, "L2": 20.0, "L3": 26.0}[register] * s
    ops: list[dict[str, Any]] = []
    for entity_id, entity in visible.items():
        intensity = max(0.0, 1.0 - _age_days(entity, now) / GLOW_WINDOW_DAYS)
        if intensity <= 0.08:
            continue
        px, py = positions[entity_id]
        ops.append({"op": "glow", "xy": (px, py), "r": radius, "color": GLOW, "intensity": intensity})
    return ops


def _glyph_ops(entity: dict[str, Any], px: float, py: float, color: tuple[int, int, int], size: float) -> list[dict[str, Any]]:
    """Shape = kind. Returns the glyph ops centered at (px, py)."""
    kind = entity.get("type")
    fill = (*color, 255)
    if kind == "claim":
        pts = [
            (px + size * math.cos(a), py + size * math.sin(a))
            for a in (math.pi / 6 + i * math.pi / 3 for i in range(6))
        ]
        return [{"op": "poly", "pts": pts, "fill": fill, "outline": (*INK, 60), "width": 1}]
    if kind == "experiment":
        w, h = size * 1.9, size * 1.4
        return [{
            "op": "rect",
            "xy": (px - w / 2, py - h / 2, px + w / 2, py + h / 2),
            "fill": fill,
            "outline": (*INK, 60),
            "width": 1,
            "radius": max(2, round(size * 0.35)),
        }]
    if kind == "review":
        r = size * 0.62
        pts = [(px, py - r), (px + r, py), (px, py + r), (px - r, py)]
        return [{"op": "poly", "pts": pts, "fill": fill, "outline": (*INK, 70), "width": 1}]
    if kind == "reflection":
        r = size * 1.35
        return [
            {"op": "ellipse", "xy": (px - r, py - r, px + r, py + r), "outline": (*color, 150), "width": max(1, round(size / 4))},
            {"op": "ellipse", "xy": (px - r * 0.55, py - r * 0.55, px + r * 0.55, py + r * 0.55), "fill": (*color, 210)},
        ]
    # resource: document glyph with a folded corner
    w, h = size * 1.3, size * 1.6
    fold = w * 0.34
    x0, y0, x1, y1 = px - w / 2, py - h / 2, px + w / 2, py + h / 2
    body = [(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)]
    return [
        {"op": "poly", "pts": body, "fill": fill, "outline": (*INK, 60), "width": 1},
        {"op": "poly", "pts": [(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], "fill": (*_blend(color, BG, 0.45), 255)},
    ]


def _pulse_ops(px: float, py: float, size: float) -> list[dict[str, Any]]:
    blue = EXPERIMENT_STATUS_COLORS["running"]
    ops = []
    for factor, alpha in ((1.55, 120), (2.0, 60)):
        r = size * factor
        ops.append({"op": "ellipse", "xy": (px - r, py - r, px + r, py + r), "outline": (*blue, alpha), "width": 2})
    return ops


def _l1_priority(entity: dict[str, Any]) -> int:
    """Label ranking at L1 — claims (region seeds) and running work first, then
    experiments by how much testing they anchor. Resource children don't count
    (they're off-board at L1)."""
    kind = entity.get("type")
    if str(entity.get("status")) == "running":
        return 100
    if kind == "claim":
        return 60
    if kind == "experiment":
        return 30
    return 5


def _l1_ops(entity: dict[str, Any], px: float, py: float, color: tuple[int, int, int], s: float) -> list[dict[str, Any]]:
    size = 11 * s
    ops = _glyph_ops(entity, px, py, color, size)
    if str(entity.get("status")) == "running":
        ops = _pulse_ops(px, py, size) + ops
    if entity.get("pinned"):
        ops.append({"op": "ellipse", "xy": (px - size - 3 * s, py - size - 3 * s, px - size + 1 * s, py - size + 1 * s), "fill": (*PIN, 230)})
    return ops


# A named metric: a plausible metric name, then ':'/'=' or whitespace, then a
# number. Float metrics (val_bpb 0.978764) are the real headline; bare integers
# ("by 25") are usually prose, so they are a last resort.
_METRIC_NAME = r'[A-Za-z][A-Za-z0-9_.%/-]{1,20}'
_FLOAT_METRIC_RE = re.compile(rf'\b({_METRIC_NAME})[\s:=]+(-?\d+\.\d+)\b', re.ASCII)
_INT_METRIC_RE = re.compile(rf'\b({_METRIC_NAME})[\s:=]+(-?\d{{1,6}})\b', re.ASCII)
_METRIC_STOPWORDS = frozenset({"by", "at", "to", "of", "on", "in", "is", "the", "a", "an", "vs", "and", "or", "from"})


def _metric_from(text: str, pattern: re.Pattern) -> str | None:
    for match in pattern.finditer(text):
        name = match.group(1).strip(".").split("/")[-1]
        if name.lower() in _METRIC_STOPWORDS:
            continue
        return f"{name[:14]} {match.group(2)}"
    return None


def _result_headline(entity: dict[str, Any]) -> str:
    """One decision-relevant token for an experiment card — the RESULT, not the
    attempt count (design-review consensus). Prefer a float metric from the
    (JSON-stripped) conclusion; then an arrow outcome clause, then any metric,
    then the attempt index."""
    conclusion = _clean_conclusion(str(entity.get("text2") or ""))
    metric = _metric_from(conclusion, _FLOAT_METRIC_RE)
    if metric:
        return metric
    if "→" in conclusion:
        tail = re.split(r"[.;]", conclusion.split("→", 1)[1].strip(), 1)[0].strip()
        if tail:
            return _fit(tail, 22)
    metric = _metric_from(conclusion, _INT_METRIC_RE)
    if metric:
        return metric
    # No result to show — a retried experiment still flags its attempt (notable),
    # but a first attempt with no metric shows nothing rather than a filler
    # ordinal (design-review: never headline the attempt count as the result).
    attempt = int(entity.get("attempt_index") or 1)
    return f"attempt {attempt}" if attempt > 1 else ""


def _clean_conclusion(text: str) -> str:
    """Drop raw JSON dumps from a conclusion so the card body stays prose, never
    a sliced-mid-object blob (design-review defect)."""
    text = str(text or "")
    cut = text.find("{")
    return text[:cut].strip().rstrip(",;:") if cut != -1 else text.strip()


def _resource_filename(entity: dict[str, Any]) -> str:
    """The distinguishing identity of a resource is its filename, not its title
    (real artifacts share a title like 'run artifacts'). Lead with the file."""
    path = str(entity.get("text") or "")
    return path.rsplit("/", 1)[-1] or str(entity.get("label") or entity.get("id"))


def _resource_chip_ops(
    entity: dict[str, Any], px: float, py: float, color: tuple[int, int, int], s: float
) -> list[dict[str, Any]]:
    """Compact L2 resource: doc glyph + filename (the distinguishing part). Full
    detail (path + id) is one zoom deeper at L3 — progressive disclosure keeps
    the L2 cluster from drowning in near-identical artifact cards."""
    r = 6 * s
    ops = _glyph_ops(entity, px - r, py, color, r)
    ops.append({
        "op": "text",
        "xy": (px + r + 4 * s, py),
        "text": _fit(_resource_filename(entity), 22),
        "size": round(10 * s),
        "color": MUTED,
        "anchor": "lm",
    })
    return ops


def _card_ops(
    entity: dict[str, Any],
    px: float,
    py: float,
    color: tuple[int, int, int],
    s: float,
    register: str,
) -> list[dict[str, Any]]:
    """L2 entity cards / L3 full-detail cards. Reviews stay docked diamonds;
    resources are compact chips at L2 (context) and full cards at L3 (detail)."""
    ops: list[dict[str, Any]] = []
    if entity.get("type") == "resource" and register == "L2":
        return _resource_chip_ops(entity, px, py, color, s)
    if entity.get("type") == "review":
        size = (13 if register == "L3" else 10) * s
        ops.extend(_glyph_ops(entity, px, py, color, size))
        label = str(entity.get("verdict") or "")
        if register == "L3":
            label = f"{entity.get('id')}  {entity.get('role') or ''} · {label}"
        ops.append({
            "op": "text",
            "xy": (px, py + size + 3 * s),
            "text": _fit(label, 44),
            "size": round((10 if register == "L3" else 9) * s),
            "color": MUTED,
            "anchor": "ma",
            "halo": True,  # knockout so the label never underlaps an adjacent card
        })
        return ops

    l3 = register == "L3"
    kind = entity.get("type")
    w = (312 if l3 else 168) * s
    title_size = round((14 if l3 else 12) * s)
    body_size = round(11 * s)
    chip_size = round((10 if l3 else 9) * s)
    line_h = body_size + round(4 * s)
    id_size = round((10.5 if l3 else 9) * s)
    pad = round(10 * s)
    glyph_r = round(8 * s)  # enlarged so kind reads without squinting at the badge
    # Title wraps to 2 lines before ellipsizing at L3 so a node is always named
    # (_wrap hard-caps each line so long hyphenated exp names can't overflow).
    title_chars = 34 if l3 else 18
    title_lines = _wrap(str(entity.get("label") or entity.get("id")), title_chars, 2 if l3 else 1)
    body_lines: list[str] = []
    if l3:
        # L3 is the terminal register — grow the card to fit the full statement
        # (up to a generous cap) rather than cutting it mid-sentence.
        body_lines = _wrap(str(entity.get("text") or ""), 42, 7)
        outcome = _clean_conclusion(str(entity.get("text2") or ""))
        if outcome:
            body_lines += _wrap("→ " + outcome, 42, 4)
    title_h = len(title_lines) * (title_size + round(3 * s))
    id_h = id_size + round(5 * s)
    h = (round(30 * s) + title_h + chip_size + round(12 * s) + id_h + len(body_lines) * line_h
         ) if l3 else (round(24 * s) + title_h + chip_size + round(10 * s) + id_h)
    x0, y0 = px - w / 2, py - h / 2
    if str(entity.get("status")) == "running":
        ops.extend(_pulse_ops(px, py, min(w, h) / 2 * 0.9))
    ops.append({
        "op": "rect",
        "xy": (x0, y0, x0 + w, y0 + h),
        "fill": (*CARD_BG, 248),
        "outline": (*color, 255),
        "width": max(1, round(2 * s)),
        "radius": round(7 * s),
    })
    # Kind spine: a colored bar on the left edge so kind is legible in
    # peripheral vision, not only from the corner glyph.
    spine = round(4 * s)
    ops.append({"op": "rect", "xy": (x0, y0, x0 + spine, y0 + h), "fill": (*color, 255),
                "radius": 0})
    ops.extend(_glyph_ops(entity, x0 + pad + glyph_r, y0 + pad + glyph_r, color, glyph_r))
    tx = x0 + pad + glyph_r * 2 + round(6 * s)
    ty = y0 + pad
    for line in title_lines:
        ops.append({"op": "text", "xy": (tx, ty), "text": line, "size": title_size,
                    "color": INK, "bold": True, "anchor": "la"})
        ty += title_size + round(3 * s)
    # status chip + result headline row
    chip_text = str(entity.get("status") or entity.get("kind") or "")
    chip_y = max(ty, y0 + pad + glyph_r * 2) + round(4 * s)
    chip_h = chip_size + round(6 * s)
    chip_w = round((len(chip_text) * chip_size * 0.62) + 12 * s)
    ops.append({"op": "rect", "xy": (x0 + pad, chip_y, x0 + pad + chip_w, chip_y + chip_h),
                "fill": (*color, 255), "radius": round(chip_h / 2)})
    ops.append({"op": "text", "xy": (x0 + pad + chip_w / 2, chip_y + chip_h / 2),
                "text": chip_text, "size": chip_size, "color": CARD_BG, "anchor": "mm"})
    _headline_ops(ops, entity, x0, w, pad, chip_y, chip_h, chip_size, s)
    # Entity id at BOTH L2 and L3 (short at L2) — the agent's action bridge, so
    # it can act after two snapshots, not three.
    eid = str(entity.get("id"))
    id_text = eid if l3 else _fit(eid, 16)
    id_y = chip_y + chip_h + round(5 * s)
    ops.append({"op": "text", "xy": (x0 + pad, id_y), "text": id_text, "size": id_size,
                "color": INK_ID, "anchor": "la"})
    if l3:
        ty2 = id_y + id_size + round(7 * s)
        for line in body_lines:
            ops.append({"op": "text", "xy": (x0 + pad, ty2), "text": line, "size": body_size,
                        "color": _blend(INK, MUTED, 0.2), "anchor": "la"})
            ty2 += line_h
    if entity.get("pinned"):
        r = round(3.4 * s)
        ops.append({"op": "ellipse", "xy": (x0 + w - r * 2 - 3 * s, y0 + 3 * s, x0 + w - 3 * s, y0 + r * 2 + 3 * s), "fill": (*PIN, 230)})
    return ops


def _headline_ops(
    ops: list[dict[str, Any]], entity: dict[str, Any], x0: float, w: float,
    pad: float, chip_y: float, chip_h: float, chip_size: float, s: float,
) -> None:
    """Right-aligned headline on the chip row: the experiment RESULT, the claim
    CONFIDENCE as a colored strength pill, or the review verdict."""
    kind = entity.get("type")
    cy = chip_y + chip_h / 2
    if kind == "claim":
        conf = str(entity.get("confidence") or "")
        if not conf:
            return
        pill_color = CONFIDENCE_PILL.get(conf, MUTED)
        pw = round((len(conf) * chip_size * 0.6) + 12 * s)
        ph = chip_size + round(6 * s)
        px0 = x0 + w - pad - pw
        ops.append({"op": "rect", "xy": (px0, cy - ph / 2, px0 + pw, cy + ph / 2),
                    "fill": (*pill_color, 255), "radius": round(ph / 2)})
        ops.append({"op": "text", "xy": (px0 + pw / 2, cy), "text": conf,
                    "size": chip_size, "color": CARD_BG, "anchor": "mm"})
        return
    # Only experiments carry a right-side headline (their result). Resources
    # already state their kind in the chip — no redundant echo.
    if kind == "experiment":
        ops.append({"op": "text", "xy": (x0 + w - pad, cy), "text": _fit(_result_headline(entity), 22),
                    "size": round(11 * s), "color": _blend(INK, MUTED, 0.15),
                    "bold": True, "anchor": "rm"})


def _margin_ops(viewport: Viewport, register: str) -> list[dict[str, Any]]:
    """A1-style margin refs — the agent's addressing scheme ("zoom into C4")."""
    s = viewport.scale
    band = round(18 * s)
    z = viewport.zoom * viewport.scale
    ops: list[dict[str, Any]] = [
        {"op": "rect", "xy": (0, 0, viewport.w, band), "fill": (*BG, 216)},
        {"op": "rect", "xy": (0, band, band + round(6 * s), viewport.h), "fill": (*BG, 216)},
    ]
    world_left = viewport.cx - viewport.w / 2 / z
    world_top = viewport.cy - viewport.h / 2 / z
    col = math.floor(world_left / CELL)
    while True:
        cx_world = (col + 0.5) * CELL
        px = viewport.to_px(cx_world, 0)[0]
        if px > viewport.w:
            break
        label = cell_label(cx_world, viewport.cy)
        if label and px > band:
            letters = label.rstrip("0123456789")
            ops.append({"op": "text", "xy": (px, round(4 * s)), "text": letters, "size": round(10 * s), "color": MUTED, "anchor": "ma"})
        col += 1
    row = math.floor(world_top / CELL)
    while True:
        cy_world = (row + 0.5) * CELL
        py = viewport.to_px(0, cy_world)[1]
        if py > viewport.h:
            break
        label = cell_label(viewport.cx, cy_world)
        if label and py > band:
            digits = label[len(label.rstrip("0123456789")):]
            ops.append({"op": "text", "xy": (round(4 * s), py), "text": digits, "size": round(10 * s), "color": MUTED, "anchor": "lm"})
        row += 1
    ops.append({
        "op": "text",
        "xy": (viewport.w - round(8 * s), viewport.h - round(7 * s)),
        "text": f"{register} · zoom {viewport.zoom:.2f}",
        "size": round(10 * s),
        "color": MUTED,
        "anchor": "rs",
    })
    return ops


# ---- rasterizing ------------------------------------------------------------

_FONTS: dict[int, Any] = {}


def _font(size: int) -> Any:
    from PIL import ImageFont

    if size not in _FONTS:
        _FONTS[size] = ImageFont.truetype(str(FONT_PATH), size=size)
    return _FONTS[size]


def _draw_dashed(draw: Any, p0: tuple[float, float], p1: tuple[float, float], color: Any, width: int, dash: tuple[int, int]) -> None:
    length = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    if length <= 0:
        return
    on, off = max(1, dash[0]), max(1, dash[1])
    ux, uy = (p1[0] - p0[0]) / length, (p1[1] - p0[1]) / length
    pos = 0.0
    while pos < length:
        end = min(pos + on, length)
        draw.line(
            [(p0[0] + ux * pos, p0[1] + uy * pos), (p0[0] + ux * end, p0[1] + uy * end)],
            fill=color,
            width=width,
        )
        pos = end + off


def _draw_edge(draw: Any, op: dict[str, Any]) -> None:
    frm, to = op["frm"], op["to"]
    if op.get("dash"):
        _draw_dashed(draw, frm, to, op["color"], op["width"], op["dash"])
    else:
        draw.line([frm, to], fill=op["color"], width=op["width"])
    if op.get("arrow"):
        angle = math.atan2(to[1] - frm[1], to[0] - frm[0])
        size = 4 + op["width"] * 2
        pts = [
            to,
            (to[0] - size * math.cos(angle - 0.45), to[1] - size * math.sin(angle - 0.45)),
            (to[0] - size * math.cos(angle + 0.45), to[1] - size * math.sin(angle + 0.45)),
        ]
        draw.polygon(pts, fill=op["color"])


def _paste_glow(image: Any, op: dict[str, Any]) -> None:
    from PIL import Image, ImageDraw

    r = int(op["r"])
    if r <= 0:
        return
    tile = Image.new("RGBA", (r * 2, r * 2), (0, 0, 0, 0))
    tile_draw = ImageDraw.Draw(tile)
    color = op["color"]
    for step in range(4, 0, -1):
        radius = r * step / 4
        # Subtle rim-light, not a smear: keep the peak alpha modest.
        alpha = round(op["intensity"] * 15 * (5 - step))
        tile_draw.ellipse(
            (r - radius, r - radius, r + radius, r + radius),
            fill=(*color, alpha),
        )
    # alpha_composite rejects out-of-bounds dests; crop the tile to the canvas.
    x, y = int(op["xy"][0]) - r, int(op["xy"][1]) - r
    left, top = max(0, -x), max(0, -y)
    right = tile.width - max(0, x + tile.width - image.width)
    bottom = tile.height - max(0, y + tile.height - image.height)
    if left >= right or top >= bottom:
        return
    tile = tile.crop((left, top, right, bottom))
    image.alpha_composite(tile, dest=(max(0, x), max(0, y)))


def _is_translucent(op: dict[str, Any]) -> bool:
    for key in ("fill", "outline", "color"):
        value = op.get(key)
        if isinstance(value, tuple) and len(value) == 4 and value[3] < 255:
            return True
    return False


class _Canvas:
    """RGBA canvas with real alpha blending. Pillow's ImageDraw REPLACES
    pixels (alpha included) rather than compositing, so translucent ops
    accumulate on an overlay that is alpha-composited into the base before
    any opaque op draws — preserving scene order; overlapping translucent
    ops within one run replace each other, which is what dense fields want."""

    def __init__(self, w: int, h: int) -> None:
        from PIL import Image, ImageDraw

        self._image_module = Image
        self._draw_module = ImageDraw
        self.base = Image.new("RGBA", (int(w), int(h)), (*BG, 255))
        self._base_draw = ImageDraw.Draw(self.base)
        self._overlay = None
        self._overlay_draw = None

    def draw(self, *, translucent: bool) -> Any:
        if not translucent:
            self.flush()
            return self._base_draw
        if self._overlay is None:
            self._overlay = self._image_module.new("RGBA", self.base.size, (0, 0, 0, 0))
            self._overlay_draw = self._draw_module.Draw(self._overlay)
        return self._overlay_draw

    def flush(self) -> None:
        if self._overlay is not None:
            self.base.alpha_composite(self._overlay)
            self._overlay = None
            self._overlay_draw = None


def rasterize(scene: list[dict[str, Any]], w: int, h: int) -> bytes:
    """Scene ops -> PNG bytes. Deterministic for identical inputs."""
    import io

    canvas = _Canvas(w, h)
    for op in scene:
        kind = op["op"]
        if kind == "glow":
            canvas.flush()
            _paste_glow(canvas.base, op)
            continue
        draw = canvas.draw(translucent=_is_translucent(op))
        if kind == "line":
            draw.line(op["pts"], fill=op["color"], width=op.get("width", 1))
        elif kind == "edge":
            _draw_edge(draw, op)
        elif kind == "rect":
            draw.rounded_rectangle(
                op["xy"],
                radius=op.get("radius", 0),
                fill=op.get("fill"),
                outline=op.get("outline"),
                width=op.get("width", 1),
            )
        elif kind == "poly":
            draw.polygon(op["pts"], fill=op.get("fill"), outline=op.get("outline"), width=op.get("width", 1))
        elif kind == "ellipse":
            draw.ellipse(op["xy"], fill=op.get("fill"), outline=op.get("outline"), width=op.get("width", 1))
        elif kind == "text":
            size = max(6, int(op["size"]))
            if op.get("halo"):
                # Light outline so labels stay legible over hulls, glows, and
                # the grid — the single biggest scanability win on dense boards.
                stroke_w, stroke_fill = max(2, size // 5), (*BG, 255)
            elif op.get("bold"):
                stroke_w, stroke_fill = max(1, size // 16), (*op["color"], 255)
            else:
                stroke_w, stroke_fill = 0, None
            draw.text(
                op["xy"],
                op["text"],
                font=_font(size),
                fill=(*op["color"], 255),
                anchor=op.get("anchor", "la"),
                stroke_width=stroke_w,
                stroke_fill=stroke_fill,
            )
    canvas.flush()
    buffer = io.BytesIO()
    canvas.base.convert("RGB").save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()

# Research Map v1 — the shared visual board

Status: approved design, not yet implemented (July 7, 2026).
Owner surface: `research_plugin` backend + `research_state_ui`.

## Vision — founder's words, verbatim

> Can we build a full resolution map/whiteboard of our research? When you zoom
> into a particular section, you get more details about that idea.
>
> This would be consumed by humans and agents. (We need to build literal zoom
> and pan features for agents)
>
> Features (traceability) (spatial relationship mapping) (spatial
> development/progression mapping) high level scanability (non-textual feature
> richness)
>
> The hard line of this feature is that everything needs to be literally
> visual. We will be taking snapshots of the board and sending that to the
> agent, similarly the user will be zooming and panning and their view will be
> visual. They will see exactly the same thing.

The hard line is the product. It buys:

- **Deixis.** Human and agent share a "there" — "the top-right cluster looks
  dead, why?" is answerable because both parties see the same pixels.
- **Non-textual bandwidth.** One snapshot of 200 entities (~1.5k tokens)
  carries gestalt no JSON dump can: density, color-coded health, cluster
  shape, the desaturated corner where a direction died.
- **Progressive disclosure = context economics.** Zoom is context management;
  the agent orients wide, then spends tokens only on its working region.
- **Traceability as literal trails.** "What did it lead to" is a visible path,
  not a query.
- **The map is a knowledge store.** Position, adjacency, and region membership
  are information that does not exist in the DB. A human dragging two ideas
  together is knowledge authoring the agent perceives in the next snapshot.

## Locked decisions (founder, July 7 2026)

1. **Layout authority: hybrid.** Deterministic auto-placement for new
   entities; humans can drag/pin, and pinned positions are permanent authored
   knowledge the layout respects. Unpinned entities never move either —
   append-mostly placement (see Placement).
2. **Agents are perceive-only in v1.** Tools: overview / snapshot / locate.
   No agent writes to the board. Mutation of research state stays with the
   existing MCP tools, bridged by entity ids readable at deep zoom.
3. **Time = traces on the living board.** Freshness glow, staleness
   desaturation, chronological growth direction. No time-scrub in v1.

## Architecture: one renderer, two consumers

The parity constraint ("they will see exactly the same thing") forbids two
renderers that merely try to agree. Therefore:

- A **brain-side raster renderer** (Pillow; deterministic pure function of DB
  state + viewport + an injected `now`) produces PNGs for any viewport
  `(cx, cy, zoom, w, h)` in world coordinates.
- The **UI displays those server-rendered pixels** (slippy-map style: fetch
  viewport render, debounced on pan/zoom; optionally fetch 2× and CSS-transform
  between fetches for smoothness). A thin client overlay does hover/click/drag
  from a positions JSON endpoint — interaction chrome only, never content.
- The **agent snapshot tools return the same renders.**
- v1 renders per-viewport with a cache keyed on
  `(layout_version, viewport, size)`. Tiles are a later optimization if
  profiling demands (Papyrus lesson: versioned publishes of rendered
  artifacts age well) — entity counts per project are tens-to-low-hundreds,
  so per-viewport is fine.
- Determinism requirements: bundle one font file in-repo (e.g. DejaVu Sans);
  renderer takes `now` as a parameter (freshness decay must be testable);
  no wall-clock or randomness inside the render path.

## Data model

New table `map_layout`, owned by a new `backend/services/map.py`
(register in `tests/structure/test_module_boundaries.py` FILE_MODULES /
TABLE_OWNERS — the structure tests will fail until you do):

```
map_layout(
  project_id TEXT NOT NULL,
  entity_id  TEXT NOT NULL,          -- exp_/claim_/res_/rev_/syn_ ...
  entity_type TEXT NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL,  -- world coordinates
  pinned INTEGER NOT NULL DEFAULT 0, -- human-authored position
  placed_seq INTEGER NOT NULL,       -- deterministic placement order
  created_at TEXT NOT NULL,
  PRIMARY KEY (project_id, entity_id)
)
```

`layout_version` for cache keys = digest of (row count, max placed_seq,
pin fingerprint) — cheap query, changes iff the board changes.

**Boundary rule:** the map service touches only `map_layout` via SQL. All
entity/edge/freshness data comes through the *public read APIs of sibling
services* injected at composition time (the pattern `tool_handlers.py`
already uses) — never raw SQL against tables it doesn't own.

## Placement — deterministic, append-mostly

Spatial memory is the product; re-layout is data loss. Rules:

- Placement sync runs on map read: every entity lacking a position is placed,
  in deterministic order (interleave services' listings by `created_at`, tie-
  break by `created_seq`/id — insertion-order columns already exist on every
  table via `next_created_seq`).
- **Parent resolution** (first match wins): experiment → the claim it tests;
  resource → the experiment it's associated to; review → its target entity;
  reflection → project-level ring; claim → parent entity if derived from one,
  else root. (Verify exact FK/association fields against
  `backend/state/store.py` SCHEMA and each service — do this inventory first.)
- **Roots** (no parent) seed regions: placed on a golden-angle spiral around
  the project origin with generous spacing.
- **Children** take golden-angle radial slots around their parent, nearest free
  ring first (occupancy = min-distance check), angle jitter seeded by
  `hash(entity_id)` — organic-looking but reproducible.
- **Never move a placed entity.** Growth direction thereby becomes visible
  history (arcs radiate chronologically) — this *is* the progression mapping.
- **Pins:** `POST /map/pin` sets `pinned=1, x, y` (UI drag). Auto-placement
  treats pinned positions as obstacles. Unpin restores nothing (position
  stays; it just becomes non-authored again).

## Zoom registers (semantic zoom)

Each band is an editorial design, not a scale factor (Papyrus: cartographic
register — the vocabulary changes per zoom, don't just scale text):

| Band | Content |
|---|---|
| **L0 — program shape** | Region blobs/hulls around root+descendants, health tint per region, entity dots, edges faint, no text except short region names. Answers "what is this project and where is it alive?" with zero reading. |
| **L1 — arcs & clusters** | Entity glyphs, 1-line labels on roots/majors, lineage edges with arrowheads, freshness glow visible. |
| **L2 — entity cards** | Name, kind icon, status chip, one key number (experiment metric / claim confidence), verdict color. |
| **L3 — full detail** | Wrapped claim/conclusion text, review verdicts, resource names, and the **entity id verbatim** — the bridge from perception to the mutation tools. |

Thresholds live in one table in the renderer.

## Visual encoding (non-textual feature richness)

- **Shape = kind**: claim hexagon, experiment rounded-rect, resource document
  glyph, review small diamond docked to its target, reflection soft halo ring.
- **Color = state**: experiment status palette (running = active blue with
  pulse ring, complete = green, failed/abandoned = muted red/gray), claim
  confidence saturation, review verdict chips.
- **Freshness = glow**: halo intensity decays with last-event age (events
  table has `target_id` + `created_at`; one grouped query gives last-touched
  per entity). ~7-day decay.
- **Staleness = desaturation**: untouched >30 days lightens/desaturates.
- **Edges by relation**: tests/supports solid; refutes dashed warm; produced/
  derived thin gray; reviewed dotted.
- **Grid frame**: subtle world-anchored grid with A1-style refs in snapshot
  margins — the agent's addressing scheme ("zoom into C4").

## HTTP surface (brain, FastAPI)

- `GET /projects/{pid}/map/snapshot?cx&cy&zoom&w&h` → `image/png`
  (no params = fit-all overview). Serving mirrors `feed_http.py`'s image
  endpoints (auth, nosniff).
- `GET /projects/{pid}/map/state` → positions/bounds/layout_version JSON.
  **UI hit-testing and drag only — never an agent surface** (hard line).
- `POST /projects/{pid}/map/pin` `{entity_id, x, y}` / `.../unpin` — UI only.

## MCP tools (stdio proxy)

- `map.overview()` → fit-all snapshot
- `map.snapshot(cx, cy, zoom)` or `map.snapshot(cell="C4")` → snapshot
- `map.locate(entity_id)` → viewport + snapshot centered on the entity

Image return path: **prefer MCP image content blocks** from the proxy. If the
proxy's protocol layer doesn't support image blocks yet, fallback: the data
plane writes the PNG under `.research_plugin/map_snapshots/` and the tool
returns the path; the agent Reads it (Claude Code renders images from Read).
Verify which path works first; pixel parity holds either way.

## UI (research_state_ui)

Route `/p/:id/map`. Server-rendered viewport image as the canvas; pan/zoom
gestures update the viewport (debounced refetch); hover tooltip + click-through
to entity pages via the `/map/state` overlay; drag-to-pin. Minimal chrome:
zoom controls, fit button, register indicator. Existing token palette and
Zustand patterns.

## Prior art and imported lessons

- **Papyrus cartography** (maps.rapidreview.io, July 2026 work): stability is
  sacred (editorial overrides exist precisely because regenerated labels
  destroy curation — pins are our overrides); registers per zoom band;
  versioned publishes of rendered artifacts; opacity/gamma tuning decides
  scanability of dense fields.
- **ARIS review** (July 2026, see memory `reference-aris-review`): the
  best-known autonomous-research project shipped *no live research-state
  surface* — its wiki cockpit is named in its own skill and unbuilt. This map
  is that missing surface; validated gap. Two disciplines to import: DB stays
  canonical and the render is a pure derived view (their MD-canonical/
  rendered-HTML pattern), and layout/render must be a deterministic function
  of state — no AI-in-the-loop at render time (their `figure-spec` ethos).
- **The feed** (this repo): the feed narrates *events*; the map holds *state*.
  Feed posts are not placed on the board in v1; `map.locate` + posts' `ref`
  field make feed→map jumps a natural v2.

## Explicitly out of v1

Agent annotations/pins, time-scrub, feed posts on the board, ghost trails,
tile server, region auto-naming beyond root names, cross-project maps.

## Phasing (land P1 as its own reviewable unit — parity before UI polish)

1. **P1**: `map.py` service (table, placement sync, pins) + renderer (L0–L3)
   + snapshot/state/pin endpoints + tests.
2. **P2**: MCP tools + proxy image path + demo seeds (extend
   `scripts/_feed_demo_server.py` or a sibling `_map_demo_server.py`).
3. **P3**: UI page (view, hover, click, drag-to-pin).
4. **P4**: encoding polish against the demo project; founder review.

## Testing

- stdlib unittest only (`PYTHONPATH=. .venv/bin/python -m unittest discover -s tests`).
- Placement: determinism across runs; append-only invariance (new entities
  never move old ones); pin respect.
- Render: identical PNG bytes for identical (fixture, viewport, now) — pinned
  font, injected clock; per-register content assertions (id text present at
  L3, absent at L1) can decode-and-scan rather than byte-compare if fonts
  prove platform-unstable.
- Structure: `map_layout` ownership registered; no cross-module SQL.

## Open items for the implementer to verify before coding

1. Exact lineage/association fields (experiments↔claims, resource
   associations, review targets, reflection coverage) from
   `backend/state/store.py` SCHEMA + each service.
2. MCP image content-block support in the stdio proxy (pick return path).
3. Pillow as a new brain dependency (`pyproject`) — the stdio proxy must stay
   stdlib-only; rendering is brain-side so this is fine, but confirm packaging.
4. Realistic entity counts from demo seeds to size default world spacing.

# Handoff: Research State UI — Mobile Redesign

## Overview

A ground-up redesign of the **mobile surface** of `research_state_ui` (the browser
frontend for the Research Plugin — the human supervisor's live window into an
autonomous research-agent workflow). This handoff covers the mobile app's home,
primary tabs, experiment detail, and the mobile MLflow ledger, rebuilt around a
minimalist "One Surface" design language and a new light/dark theme derived from
the project's own brand deck.

The redesign is **mobile-only**. The desktop layout is untouched except for shared
color tokens (see Design Tokens → note on desktop).

## About the design files

The files in `design_references/` are **design references authored in HTML** —
prototypes that show the intended look, layout, and behavior. They are **not**
production code to copy verbatim. The task is to **recreate these designs inside
the existing `research_state_ui` React + Vite codebase**, using its established
patterns: components in `src/`, the `html[data-surface="mobile"]` scoping
convention, `src/styles/mobile.css`, the `useProjectStore` selectors, and the
`api.js` client. Where a prototype hard-codes sample data (experiment names,
metric values), wire it to the real store/endpoints noted per screen below.

Open any `.dc.html` file in a browser to view it. They are self-contained. Each
prototype shows the screen(s) in **Luna (light)** and **Umbra (dark)** side by side.

### File index
- `design_philosophy.md` — the canonical four-pillar framework (mirror of the repo's `docs/design_philosophy.md`). The "why" behind every rule below.
- `design_references/Color System.dc.html` — full light/dark token tables + usage rules, applied to the Home screen.
- `design_references/Mobile Redesign Sketches.dc.html` — the low-fi wireframes that established the direction: the "One Surface" language, the one-scroll experiment detail, and the **right-edge scrub rail** (rest + engaged states). Reference this for the experiment-detail interaction.
- `design_references/Home Page.dc.html` — home concepts; **turn 3 (`3a`/`3b`) is the approved "Instrument snapshot" home** (light + dark). Turns 1–2 are superseded exploration, kept for context.
- `design_references/Feed and Experiments.dc.html` — Feed and Experiments screens (light + dark).
- `design_references/MLflow.dc.html` — mobile MLflow ledger (light + dark).

## Fidelity

**High-fidelity.** Colors, typography, spacing, and interactions are final. Recreate
pixel-faithfully using the codebase's existing components and CSS conventions.
Exact hex values, font sizes, and layout rules are specified below and in the
prototypes. (The wireframe file is the one lo-fi asset — it exists only to convey
the scrub-rail interaction and the one-scroll structure.)

---

## The design language — "One Surface"

Derived from the project's canonical **`docs/design_philosophy.md`** (the "Unified
Minimalist Design Framework" — four pillars: Dynamic Continuity, Absolute
Functional Clarity, Subconscious Frictionlessness, Micro-Faceted Brilliance, plus
the governing law *"form must articulate the energy of the interaction"*). That
document is the source of truth; a copy is bundled here as `design_philosophy.md`
and it also lives in the repo at `docs/design_philosophy.md` — **read it first**,
and when a design decision is ambiguous, resolve it against the four pillars.

The five concrete rules below are how that framework is applied to this mobile UI:

1. **One flowing column.** No cards, no boxed containers. Content sits flush on a
   single surface. (The only exceptions: the MLflow chart grid and the Home
   snapshot band, which use a light hairline grid because charts/figures need
   containment.)
2. **Hairlines only where meaning breaks.** A 1px `--line` divider appears between
   *sections*, never around every row. Rows are separated by whitespace.
3. **The 3px color index is the only rupture.** A 3px left bar in `--accent`
   (orange) flags anything that needs the user. It is the sole decorative color moment.
4. **Orange is scarce and functional.** Orange = the universal accent: active nav
   tab, links, primary actions, and the attention index. Never decorative.
5. **The scrub rail is experiment-only.** The right-edge section rail (below) exists
   *only* inside an experiment detail; no other screen has it. It is a pure overlay
   that never reshapes content.

Typography: system UI stack (`-apple-system, system-ui, "Segoe UI", Roboto, …`).
No custom fonts. Numbers use `font-variant-numeric: tabular-nums`. Monospace
(`ui-monospace, Menlo`) only for run ids and metric keys.

---

## Design Tokens

Two themes. Light is **Luna**, dark is **Umbra**. Both were read from the brand
deck's PPTX source (accent + neutrals) — see "provenance" note at the end.

### Light — "Luna"
| Token | Hex | Role |
|---|---|---|
| `--bg` | `#F2F2F0` | app canvas |
| `--elev` | `#FFFFFF` | bars, sheets, elevated wells |
| `--soft` | `#E7E7E4` | fills, soft wells (e.g. link unfurl) |
| `--text` | `#17181A` | primary ink |
| `--muted` | `#57595C` | secondary text |
| `--faint` | `#9A9C9F` | labels, hints, meta |
| `--line` | `#E2E2DF` | hairline dividers |
| `--line2` | `#D3D3D0` | stronger borders (device, tiles) |
| `--accent` | `#FF6B35` | active · attention · links |
| `--green` | `#1F7A3A` | running / good / positive trend |
| `--steel` | `#3F6699` | neutral info / queued / ready |
| `--red` | `#C0392B` | failed / danger |

### Dark — "Umbra"
| Token | Hex | Role |
|---|---|---|
| `--bg` | `#121213` | app canvas (near-black, faint cool undertone) |
| `--elev` | `#1B1B1D` | bars, sheets |
| `--soft` | `#232325` | fills |
| `--text` | `#ECECEE` | primary ink |
| `--muted` | `#9A9CA0` | secondary text |
| `--faint` | `#616367` | labels, hints |
| `--line` | `#2A2A2C` | hairline dividers |
| `--line2` | `#39393C` | stronger borders |
| `--accent` | `#FF7A45` | active · attention · links (slightly brightened for dark) |
| `--green` | `#5FC46E` | running / good |
| `--steel` | `#7098CC` | neutral info |
| `--red` | `#E5675A` | failed / danger |

### Radii / spacing
- Radii: rows/hairlines are square; grids/wells `10–14px`; device frame `~34px`.
- Section rhythm: ~13–16px between a section's last row and the next `--line`.
- Touch targets: nav tabs ≥ 52px tall; rows ≥ 44px.

### Note on desktop / where tokens live
In the repo, tokens live in `src/styles/global.css` under `:root` (light) and
`html[data-theme="dark"]` (dark). Adopting Luna/Umbra there updates **both**
surfaces — which is intended (mobile↔desktop consistency). The current desktop
accent is a blue `--active`; the redesign replaces it with orange `#FF6B35`.
Map the existing token names to these values (existing names: `--bg`, `--bg-elev`,
`--bg-soft`, `--text`, `--muted`, `--faint`, `--line`, `--line-strong`, `--active`,
`--supports`, `--refutes`, etc.). Suggested mapping: `--active` → accent,
`--supports` → green, `--refutes` → red, add a steel/info token, `--bg-elev` → elev,
`--bg-soft` → soft. Confirm desktop still reads well after the swap.

---

## Screens / Views

### 1. Home — "Instrument snapshot" (replaces the "Now" screen)
**Reference:** `Home Page.dc.html`, options `3a` (light) / `3b` (dark).
**Repo file:** `src/mobile/NowScreen.jsx` → rename/replace as `HomeScreen.jsx`.
**Purpose:** the supervisor's glance — *what's the state, what's live, what needs me* — as an instrument, not a feed.

**Layout (top → bottom, single scroll):**
- **Top bar** (`.pbar`, sticky): project name (`--text`, 14px/600), live sync (green dot + "synced 2s", `--muted`), theme toggle glyph. Same bar on every screen.
- **Standing line** (`.stand`): one row — date/time (`--muted` 12px) on the left, a tappable `2 need you →` chip (`--accent` 12px/600) on the right. No paragraph.
- **24h snapshot band** (`.tiles`): a 2×2 grid, 1px `--line` internal dividers, `14px` radius. Four tiles:
  - `experiments · 24h` — count + green `▲N` delta vs prior period
  - `GPU compute · 24h` — hours (e.g. `31h`)
  - `live now` — running count + GPU type (`8×H100`)
  - `reviews open` — count
  - Value: 24px/700 tabular; label: 10px `--muted`.
- **Live now** (`.ml` label + `.livehead`): running experiment name + elapsed; a GPU-util meter (`.umeter`, 5px bar, `--green` fill at util%, over `--soft`); a util caption ("GPU 74% · VRAM 61%" / "8×H100"); a metric line — key (`loss · last 30 steps`) + sparkline (`--green` polyline) + value `1.94` + `▼` trend.
- **Section break** (`--line`).
- **Needs you** (`.ml` + rows): compact `.prow`s — 3px `--accent` index, title (14px/600), sub (11px `--muted`). Two items max shown; tap → target.
- **Bottom nav** (`.pnav`): 4 tabs — Home / Feed / Exps / More. Active tab = `--accent`. Home glyph = house outline (new; see icons).

**Real data (wire to these):**
- Counts: `selectStats` (`home.stats`) + `selectExperiments`, `selectSandboxes`.
- Live GPU-util / VRAM: `api.getSandboxMetrics(pid, eid)` (returns `{available, metrics:{gpus:[{util, vram…}]}}`) — best-effort, only while a sandbox is up.
- Metric curve: `api.getResultsMetrics(pid, eid)` → `results_metrics` shape (see MLflow).
- **Derived client-side (no stored ledger):** "experiments · 24h" = count `experiments` with `created_at`/`updated_at` in last 24h; "GPU compute" = Σ(uptime × gpu-count) over sandboxes/runs. Label these as approximate if precision matters.
- Needs-you items: reuse the current `NowScreen` derivation (review-gated `active_experiments` where status ∈ {`design_review`,`experiment_review`}, open `reviewRequests`, sandboxes expiring soon).

### 2. Feed
**Reference:** `Feed and Experiments.dc.html` (Feed phones).
**Repo files:** `src/feed/Feed.jsx`, `src/feed/PostCard.jsx`, `src/feed/feed.css`.
**Purpose:** the agents' running commentary, low-chrome.

**Layout:** top bar + page title "Feed" (`.ptitle-lg`, 22px/600), then a flush list of posts (`.fpost`) separated by a `--line` hairline. Each post:
- Head (`.fhead`): author handle (13px/600 `--text`), optional role tag (`.frole` — 8px uppercase, 1px `--line2` border, e.g. "design"), timestamp right-aligned (`--faint` 10.5px).
- Text (`.ftext`, 13px/1.5 `--text`).
- **At most one visual:** either an entity chip (`.fref` — `↗ vision-scaling`, `--accent` 11px/600), OR a striped figure placeholder (`.fimg` — repeating-linear-gradient stripes, mono filename label), OR a soft link unfurl (`.flink` — `--soft` background well, NOT a bordered card: 44px striped thumb + host with verified `✓` + title).
- Orange appears only on the entity chip and link host.

**Real data:** existing `feedApi` / `PostCard` props (`author_handle`, `author_role`, `created_at`, `text`, `image_url`, `link_url` + `link_preview`, `ref`). Keep the existing `useAuthedImage` blob-loading and `IntersectionObserver` view-tracking; only restyle.

### 3. Experiments
**Reference:** `Feed and Experiments.dc.html` (Experiments phones).
**Repo files:** `src/mobile/ExperimentCardList.jsx`, classes in `src/styles/mobile.css`.
**Purpose:** the lifecycle list.

**Layout:** top bar + title "Experiments", then:
- **Filter row** (`.efilt`): a horizontally-scrollable underline text filter (NOT boxed chips) — `All 7 · Running 1 · Review 1 · Done 3 · Failed 1`. Active item: `--text` with a 2px `--accent` bottom rule (`box-shadow: inset 0 -2px 0 var(--accent)`). Counts in a smaller faint tabular span.
- **Rows** (`.erow`, hairline-separated): 3px left index (`.eix`) colored **by state** — `--accent` (needs you: design/experiment review), `--green` (running / complete-good), `--steel` (ready/queued/planned), `--red` (failed), `--faint` (abandoned). Then name (14px/600), status line (11px, colored to match the index), meta (`.emeta` — faint 10.5px: attempt, claims, elapsed).
- Scanning the left edge = reading the whole fleet's health. Orange edge = "needs you" without any badge.

**Real data:** `selectExperiments`; status → index color via the state map above (mirror the FSM order already in `ExperimentCardList.jsx`). Tap row → `/experiments/:id`.

### 4. Experiment detail — one scroll + scrub rail
**Reference:** `Mobile Redesign Sketches.dc.html` — options `2b` (one-scroll structure) and `3a`/`3b` (the scrub rail at rest + engaged). This is the interaction-critical screen; study those frames.
**Repo file:** `src/mobile/MobileExperimentDetail.jsx` (currently a sticky segmented control with only-active-segment mounting).
**Purpose:** everything about one experiment, without a tab strip.

**Layout & behavior:**
- Replace the top segmented control (`.mseg`) with **one continuous scroll**: sections `Status → Plan → Run → Outcomes` flow down the surface, each introduced by a `.ml` label and separated by a hairline. (Keep the FSM strip + gate banner at the top.)
- **Heavy panes load on tap.** The Run terminal must NOT mount on scroll — render a collapsed `▸ terminal — tap to attach` row that mounts `SandboxTerminal` on tap. This preserves the current "terminal polls only when open" behavior and avoids stacking pollers.
- **Right-edge scrub rail (experiment-only, pure overlay):**
  - **At rest:** a ~15px-wide translucent strip pinned to the right edge, vertically centered, showing 4 faint tick marks (one per section); the current section's tick is `--accent`. It overlays the content (content stays full-width behind it) and never reshapes layout.
  - **On touch/drag:** it widens (~104px) into a frosted panel (`background: rgba(elev, .74)` + `backdrop-filter: blur(9px)`), revealing the four section labels; the active label snaps to `--text` with a 3px `--accent` right edge. Dragging scrubs between sections; releasing snaps. It slides back to the thin rest state when released/idle.
  - Scope: **only** this screen. It must never appear on Home/Feed/Experiments/MLflow, and must never compete with the bottom nav's between-screen role.

**Real data:** unchanged from current `MobileExperimentDetail` (`api.getExperimentStatus`, `PlanSpotlight`, `SandboxTerminal`, `ReportSpotlight`, `ExperimentMetrics`). This is a presentation/navigation change, not a data change. Keep read-only (no transition buttons on mobile).

### 5. MLflow (mobile)
**Reference:** `MLflow.dc.html`.
**Repo files:** `src/pages/MlflowDashboard.jsx` + `src/components/RunMetrics.jsx` (desktop renderer) — add a mobile view; reached from the **More** sheet.
**Purpose:** the single quantitative ledger over the whole project, on a phone.

**Layout:** back eyebrow `‹ More`, title "MLflow", a lede line (`6 experiments · 14 runs · 5 metrics · updated 2m`), then:
- **Metric-focus filter** (`.mfocus`): underline text filter `All · loss · eval_acc · grad_norm · lr` (metric keys in mono). Selecting one scopes every chart below to that metric across all runs — the mobile substitute for the desktop's wide curve grid.
- **Grouped ledger:** for each experiment (`.mexp` header: name + `status · N runs`), each run (`.mrun`: mono run id + status), then a **2-up chart grid** (`.mgrid`, 1px `--line` dividers, 11px radius). Each cell (`.mcell`): metric key (mono 10px), final value (14px/700 tabular) + trend arrow (`▼`/`▲`, `--green` when heading the right way; loss down = good, acc up = good; grad_norm/lr neutral), and a gridless sparkline (`.mc-spark`, `preserveAspectRatio="none"`, `--steel`/`--green`/`--muted` stroke). Tap a curve → full chart.

**Real data:** `api.getMlflow(pid)` → `{experiments:[{…, metrics:{...results_metrics...}}]}`; per the `results_metrics` shape each run carries `metrics` (`{key:{last,min,max}}`) and `history` (`{key:[[step,value],…]}`) + `params`. Reuse `runsFromMetrics()` and the `Sparkline` component from `src/components/`.

---

## Interactions & Behavior
- **Nav:** 4-tab bottom bar on all primary screens. "Now" tab is renamed **Home** with a house-outline glyph (add to `src/mobile/icons.jsx`). Active = `--accent`.
- **Pull-to-refresh, toasts, bottom sheet (More):** keep the existing mobile primitives (`usePullToRefresh`, `Toast`, `BottomSheet`) — restyle to tokens only.
- **Scrub rail:** touch/drag interaction described in screen 4. Respect `prefers-reduced-motion` (no slide animation; snap).
- **Sync dot:** live/paused/stale states as today (`--green` live, `--faint` paused, `--red`/`--accent` stale).
- **Theme toggle:** existing `useTheme` (light → dark → system). Ensure both Luna and Umbra are wired to `data-theme`.

## State Management
No new global state. Everything reads from the existing `useProjectStore` selectors
(`selectStats`, `selectExperiments`, `selectActiveExperiments`, `selectSandboxes`,
`selectReviewRequests`, `selectEvents`) and the `api.js` endpoints named per screen.
New local state: scrub-rail open/drag position (component-local), Home's derived
24h/GPU-hours memo, MLflow metric-focus selection.

## Assets
No image assets. All glyphs are inline SVG (nav icons in `src/mobile/icons.jsx`;
add a `IconHome` house-outline). Figure placeholders in Feed are CSS
repeating-linear-gradient stripes — in production these are real post images via
the existing `useAuthedImage`. Sparklines are inline SVG `<polyline>`.

## Files (in this bundle)
- `design_philosophy.md` — canonical four-pillar framework (mirror of repo `docs/design_philosophy.md`)
- `design_references/Color System.dc.html`
- `design_references/Mobile Redesign Sketches.dc.html`
- `design_references/Home Page.dc.html`
- `design_references/Feed and Experiments.dc.html`
- `design_references/MLflow.dc.html`
- `screenshots/` — rendered PNGs (light + dark) of each screen:
  - `01-home-instrument.png`
  - `02-feed.png`
  - `03-experiments.png`
  - `04-experiment-detail-scrub-rail.png` (lo-fi — shows the rail at rest vs. mid-scrub; production uses One-Surface + Luna/Umbra styling)
  - `05-mlflow.png`

## Provenance note (colors)
Accent and neutrals were read from the project's own intro deck (PPTX theme +
placed colors), not invented: brand orange `#FF6B35` (used 14× on the slides),
warm-paper light, and a near-black dark tuned to keep a faint cool undertone so
orange pops and dense content (terminals, graphs) stays calm. Keep orange scarce.

## Not yet designed (out of scope for this handoff)
Secondary screens still on the current mobile styling: **Synthesis / reflection
waves, More sheet, Reviews, Sandboxes, Events/Debug.** These can ship on the new
tokens immediately (they'll inherit Luna/Umbra) and be re-laid-out in the One-Surface
language in a later pass.

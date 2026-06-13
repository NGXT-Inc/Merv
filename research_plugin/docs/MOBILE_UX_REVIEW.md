# Mobile UI — Screen-by-Screen UX Review & Improvement Proposal

**Status:** review, 2026-06-13.
**Scope:** audit the *as-built* mobile surface in `research_state_ui/src/mobile/`
(+ the desktop pages it reuses), diagnose where the UI fails at the edges, and
propose mobile-native improvements and a plan to close the desktop→mobile
feature gap.
**Relationship to [`MOBILE_PLAN.md`](./MOBILE_PLAN.md):** the plan is the
forward-looking build plan (Phases 0–3). This document audits what actually
shipped against a real phone viewport, finds the gaps between plan and code, and
turns them into concrete, prioritized work. Where the plan already anticipated a
problem, this review marks it **[plan-known]** and reports its current status.

> **Implementation status — 2026-06-13.** The P0/P1/most-P2 items in this review
> were implemented (all in `src/mobile/` + the surface-scoped `mobile.css`, plus
> the one shared `SandboxTerminal` visibility-pause that lands on desktop too).
> Delivered: read-only embedded terminal + iframe suppression + poller pause;
> the `BottomSheet`/`useScrollLock`/`usePullToRefresh`/`toast`/`Skeleton`/
> `SlideToConfirm`/SVG-icon primitives; adaptive polling; on-device graphs
> (`GraphOutline` + lazy fullscreen `GraphCanvasOverlay`, new Graph segment);
> SVG metric curves (`Sparkline`/`MobileMetricsPanel`); read-only `MobileClaims`/
> `MobileReviews`/`MobileProjects` + bootstrap notice; slide-to-confirm release;
> the synthesis card + counts strip on Now; reused-page CSS fixes; and a minimal
> PWA manifest. Still open: a true Web Push/badge channel, a service worker, the
> VisualDag node-list fallback, and a full card treatment for Debug — see §5.

---

## 0. What shipped vs. what was planned

The mobile surface today is **Phase 1 complete + a slice of Phase 2**, served
through the "one app, two shells" gate (`useViewport` → `MobileShell`):

| Capability | Plan phase | Built? | Notes |
|---|---|---|---|
| Viewport gate + `rsui:surface` override | 1 | ✅ | `store/useViewport.js` |
| MobileShell: app bar, 4-tab nav, More sheet | 1 | ✅ | `mobile/MobileShell.jsx` |
| NowScreen (needs-you / in-flight / sandboxes / recent) | 1 | ✅ | `mobile/NowScreen.jsx` |
| ExperimentCardList + filter chips | 1 | ✅ | `mobile/ExperimentCardList.jsx` |
| MobileExperimentDetail (segmented control) | 2 | ✅ | Status / Plan / Run / Outcomes |
| SandboxCardList + slide-to-confirm release | 2 | ✅ | now a real `SlideToConfirm` + toast/haptic (§3.5) |
| MobileResources (in-page FileTree) | 3 | ✅ | `mobile/MobileResources.jsx` |
| **Adaptive polling** (5s live → 30–60s idle) | 1 | ✅ | derived interval in `App.jsx`; 30s when idle |
| **Pull-to-refresh** | 1 | ✅ | `usePullToRefresh` wired in the shell |
| **MetricsChart** (SVG curves, no iframe) | 2 | ✅ | `Sparkline` + `MobileMetricsPanel` in Outcomes |
| **No iframes on mobile** | 2 | ✅ | `SandboxTerminal readOnly` drops the iframe tabs |
| **GraphOutline + fullscreen graph** | 3 | ✅ | `GraphOutline` + lazy `GraphCanvasOverlay`, Graph segment |
| PWA manifest / service worker / badges | 3 | ◑ | manifest + icon + apple/theme metas; no SW/badges |
| VisualDag node-list fallback | 3 (cut) | ❌ | "desktop only" notice only |

**Headline:** the *navigation and triage* story (Phase 1) is solid and genuinely
mobile-native. The *depth* story degrades in three ways — (a) several routes
still render desktop-physics pages verbatim, (b) two marquee desktop
visualizations (the experiment **figure** and **logic graph**) and the project
**synthesis** panel have no mobile presence at all, and (c) a few cross-cutting
behaviors the plan called for (adaptive polling, pull-to-refresh, no-iframes,
terminal visibility-pause) were never wired up.

**Severity legend:** 🔴 breaks/blocks on a real phone · 🟠 usable but poor ·
🟡 polish · 🟢 working well, noted for completeness.

---

## 1. Cross-cutting findings (fix once, every screen benefits)

### 1.1 🔴 Reused desktop pages are not mobilized — five routes render desktop layouts
Routes `/claims`, `/claims/:id`, `/reviews`, `/events`, `/activity`, `/debug`,
`/projects`, `/projects/new` render the **desktop page components unchanged**
(`App.jsx:80–90`). `mobile.css` only nudges a handful of shared primitives
(`.kv-row`, `.timeline-row`, `.btn`), so these pages inherit desktop-physics
layout on a 390 px screen. The worst offenders are diagnosed per-screen in §2,
but the pattern is systemic: **fixed-column grids and non-wrapping flex rows that
assume ≥720 px.**

### 1.2 🔴 The "read-only on mobile" invariant is silently violated
The plan's load-bearing safety rule (`MOBILE_PLAN.md §2`, §5): *"No
transition/approve/delete affordances on mobile. The single sanctioned mutation
is sandbox release."* The mobile-native screens honor this — but the **reused
desktop pages smuggle mutations onto the phone**:

| Route | Mutation exposed | Code |
|---|---|---|
| `/claims` | "New claim" → `api.createClaim` | `Claims.jsx:56`, `:175` |
| `/projects` | Edit/rename → `api.patchProject` | `Projects.jsx:64`, `:151` |
| `/projects/new` | `api.createProject` | `CreateProject.jsx:29` |
| `/debug` | "Clear" → `api.clearToolCalls` (`window.confirm`) | `Debug.jsx:122` |
| Run segment + Sandbox drawer | **unguarded** "Release sandbox" inside `SandboxTerminal` | `SandboxTerminal.jsx:162` |

The last one is the most dangerous: `SandboxCardList` carefully gates release
behind a two-step confirm with a running-experiment escalation
(`SandboxCardList.jsx:149–169`), but the **same `SandboxTerminal` component
rendered just below it (and on the experiment Run segment) has its own bare
`Release sandbox` button with no confirm at all.** A mis-tap kills a GPU VM. Two
release paths, two safety levels — the careful one is bypassable.

**Proposal:** add a `readOnly`/`embedded` prop to `SandboxTerminal` that hides
its action header on mobile (release flows only through the guarded
`SandboxCardList` path). For Claims/Projects, hide creation/edit affordances when
`data-surface="mobile"` (a `useViewport()` check or a CSS `display:none` on
`.page-actions` within mobile-reused pages), or replace these routes with
read-only mobile treatments (§2.6, §2.10).

### 1.3 🟠 Polling is fixed-interval, not adaptive — battery & freshness both suffer
`App.jsx:38` uses `usePolling(isMobile ? 5000 : 3000)` — a constant 5 s.
`MOBILE_PLAN.md §3.5` specified **adaptive cadence**: ~5 s only when something is
live (running experiment / open terminal), decaying to 30–60 s on a quiet Now
screen, with pull-to-refresh as the instant override. On cellular/VPN the radio
wakeup every 5 s is the dominant battery cost even when nothing is happening.
Worse, `SandboxTerminal` stacks **its own** un-throttled pollers on top (§1.4).

**Proposal:** make `usePolling` accept a function or derive the interval from
store state (`selectActiveExperiments`, running sandboxes, "is a terminal
segment mounted"). Quiet Now screen → 30 s; anything live → 5 s. Pair with
pull-to-refresh (§3.4) so the user always has an instant override.

### 1.4 🔴 `SandboxTerminal` never pauses its pollers when the tab is hidden — [plan-known]
`MobileExperimentDetail` correctly guards its status poll with
`document.visibilityState` (`MobileExperimentDetail.jsx:61–64`). But the
`SandboxTerminal` it mounts does **not**: the 3 s sandbox/metrics poll
(`SandboxTerminal.jsx:116–122`) and the 1.5 s terminal poll (`:126–134`) fire on
a bare `setInterval` with no visibility check. So when the phone locks or the
user switches apps with the Run segment open, the app keeps hammering the daemon
at 1.5 s forever. The plan flagged this exact bug ("Fix SandboxTerminal's missing
visibilitychange pause — ships to desktop too, it's a live bug",
`MOBILE_PLAN.md §Phase 2`); it is still unfixed.

**Proposal:** add `visibilitychange` pause/resume to both intervals in
`SandboxTerminal` (mirror the pattern already in `MobileExperimentDetail` and
`usePolling`). Low-risk, benefits desktop too.

### 1.5 🔴 MLflow/TensorBoard iframes still render on mobile — [plan-known]
The Run segment renders `SandboxTerminal`, which exposes **iframe tabs** for
MLflow (`:5000`) and TensorBoard (`:6006`) whenever `sandbox.dashboards[key]` is
non-empty (`SandboxTerminal.jsx:224–321`). Those URLs are typically
`127.0.0.1` SSH local-forwards owned by the *desktop* that holds the tunnel —
**unreachable from the phone** — so the tab loads a spinner forever, then a
blank/refused frame. The plan was explicit: *"No iframes on mobile, period"*
(`§Phase 2`). `SandboxCardList` already filters loopback dashboards out of its
*links* (`SandboxCardList.jsx:14–19, 92–93`), but the embedded terminal does not.

**Proposal:** on mobile, suppress the dashboard iframe tabs in `SandboxTerminal`;
for non-loopback (e.g. Modal HTTPS tunnel) URLs render an **"open ↗"** button
instead of an embed; for loopback URLs render the honest empty state ("runs over
an SSH tunnel to your desktop — archived curves below") and lean on the durable
`ResultsMetricsPanel` / a future `MetricsChart` (§3.6).

### 1.6 🟠 No skeletons, toasts, or haptics — loading & feedback feel un-native
Every async screen falls back to the literal string `Loading…`
(`MobileExperimentDetail.jsx:85`, `ClaimDetail.jsx:40`, `Reviews.jsx:37`,
`Events.jsx:128`, `Activity.jsx:179`). Successful mutations (sandbox release) just
silently refresh; failures surface as an inline `.error-message`. There are no
toasts, no skeleton placeholders, and no haptic confirmation on the one
destructive action.

**Proposal:** a tiny `Skeleton` card primitive for list/detail loads; a
lightweight `Toast` host mounted in `MobileShell` for action results; and
`navigator.vibrate(...)` on the release-confirm commit. All additive, all in
`src/mobile/`.

### 1.7 🟡 Segmented control sticky offset is hard-coded to the app-bar height
`.mseg { top: calc(44px + env(safe-area-inset-top)) }`
(`mobile.css:374–388`) assumes a 44 px app bar, but `.mbar` is ~40 px of content
+ padding (`mobile.css:29–42`). Off by a few px → a thin strip of scrolled
content can peek above the sticky segment, or the segment can overlap the bar on
some devices. **Proposal:** measure the bar (CSS var set from a `ResizeObserver`,
or `position: sticky; top: 0` on a shared header wrapper) instead of a magic
number.

### 1.8 🟡 `MobileExperimentDetail` shows the previous experiment for a tick on navigation
`statusData` is not reset when `experimentId` changes
(`MobileExperimentDetail.jsx:42–66`), so navigating experiment→experiment flashes
the *old* experiment's content until the next fetch resolves. And the landing
`segment` is only defaulted while `segment == null`
(`:72–74`), so after you've tapped a tab once, every subsequent experiment opens
on *that* tab regardless of its lifecycle (open a `complete` exp on Outcomes,
then tap a `planned` exp → you land on Outcomes, not Plan).

**Proposal:** key the component on `experimentId` (or reset `statusData`/`segment`
in an effect on id change) so each experiment loads fresh and re-defaults its
landing segment.

---

## 2. Screen-by-screen audit

### 2.1 MobileShell — app bar · bottom nav · More sheet  🟢/🟠
`mobile/MobileShell.jsx`, `mobile.css:12–196`.

**Working well:** sticky translucent app bar with project name + freshness dot +
theme toggle; 4-tab bottom nav (Now / Experiments / Activity / More) with a
needs-attention badge; safe-area padding throughout; backdrop-tap to close the
sheet; sheet auto-closes on navigation (`:53`).

**Edge cases / failures:**
- 🟠 **The More sheet is not a real bottom sheet.** The grip (`.msheet-grip`) is
  decorative — there is no swipe-to-dismiss, no snap points, no drag. On a phone
  the affordance promises a gesture that does nothing (`MobileShell.jsx:131`).
- 🟠 **"More" hides primary destinations.** Claims, Reviews, Resources,
  Sandboxes, Projects all live one extra tap deep behind More. For a supervisor,
  *Reviews* (the gate queue) is arguably as important as Activity, yet Activity
  gets a top-level tab and Reviews doesn't.
- 🟡 **Nav glyphs are Unicode symbols** (`◉ ⚗ ≋ ⋯`) — they render inconsistently
  across Android/iOS fonts and don't communicate "experiments/activity" clearly.
- 🟡 **Attention badge can read a stale count** when the daemon is unreachable
  (it's derived from last-known store state) with no "stale" affordance on the
  badge itself.
- 🟡 `ProjectSwitcher`'s popover is a desktop `position:absolute` popover
  rendered *inside* the sheet (`MobileShell.jsx:133`); it works because the chip
  is full-width, but it's a popover-in-a-sheet, not a native list.

**Proposals:**
- Build a reusable **`<BottomSheet>`** primitive (drag handle, snap points,
  velocity-based dismiss, `useScrollLock` body-fix for the iOS scroll-leak the
  plan calls out) and back the More sheet with it. Reuse it everywhere a popover
  exists today (§3.2).
- Reconsider the tab taxonomy: **Now / Experiments / Reviews / More**, or make
  the 3rd tab context-adaptive (Reviews when a gate is open, else Activity).
- Replace glyphs with a small inline SVG icon set (consistent, theotable, ARIA).
- Render the project switcher inside the sheet as a native option list, not a
  popover.

### 2.2 NowScreen — the mobile landing  🟢
`mobile/NowScreen.jsx`.

**Working well:** this is the strongest mobile screen. "Needs you" stack ordered
by urgency (expiring sandboxes → review gates → open requests), "In flight",
running sandboxes with burn/expiry, recent timeline, and a genuine
"nothing needs you" empty state. All from already-polled data — no new requests.

**Edge cases / failures:**
- 🟠 **No pull-to-refresh** — the canonical mobile gesture for "is this current?"
  is absent (only the buried More-sheet button exists). This is the screen that
  most needs it.
- 🟡 **Sandboxes section partially duplicates `SandboxCardList`** with a thinner
  card; fine, but the two should share a card component to avoid drift.
- 🟡 **No at-a-glance counts.** Desktop Home has a `Counts` stat grid
  (`Home.jsx:115–124`); Now has none, so "how many claims/experiments/open
  reviews" requires navigating. A compact counts strip would help the glance.
- 🟡 **No project synthesis presence** (see §4) — the project's headline
  narrative isn't reachable from the landing on mobile at all.

**Proposals:** add pull-to-refresh (§3.4); add an optional compact counts row;
surface a one-line **synthesis headline** card linking to a synthesis sheet
(§4.3); extract a shared `<SandboxCard>`.

### 2.3 ExperimentCardList — "What we try"  🟢
`mobile/ExperimentCardList.jsx`.

**Working well:** horizontally-scrollable FSM-ordered filter **chips** with counts
(the right mobile pattern), one card per experiment with outcome glyph, status
pill, 2-line intent clamp, attempt/claims/duration meta. Empty states per filter.

**Edge cases / failures:**
- 🟡 **No sort control** (newest-first is hard-coded, `:40`). On a large project a
  "by status / by recency / by attention" toggle would help.
- 🟡 **Intent clamp has no expand** here (the `.mcard-sub--open` class exists in
  `mobile.css:288` but isn't wired to a tap on this card) — long intents are
  truncated with no way to read them without opening the detail.
- 🟡 Chips row can grow long; a leading "all" chip that stays pinned while the
  rest scroll would aid orientation.

**Proposals:** wire tap-to-expand the intent (the CSS already exists); add a sort
menu (bottom sheet); pin the active/"all" chip.

### 2.4 MobileExperimentDetail — segmented Status/Plan/Run/Outcomes  🟠
`mobile/MobileExperimentDetail.jsx`.

**Working well:** the segmented control where **only the active segment mounts and
polls** is exactly right — it avoids the desktop detail's five-poller pile-up.
Read-only gate panel (no transition buttons) is correct per the invariant.
Landing segment is chosen by lifecycle (`defaultSegment`, `:22–27`).

**Edge cases / failures:**
- 🔴 **No figure, no logic graph.** The desktop detail's centerpiece —
  `ExperimentGraphs` (the derived **figure** ⇄ the agent-authored **logic
  graph**, `ExperimentDetail.jsx:204–210`) — is simply **not rendered on mobile**.
  This is the single biggest feature gap: the "story of the experiment" is
  invisible on the phone. (Integration plan in §4.1.)
- 🔴 **Run segment inherits the iframe + unguarded-release problems** (§1.4, §1.5).
- 🟠 **Metrics are final-values only.** Outcomes shows `ResultsMetricsPanel`
  (a value grid, `ResultsMetricsPanel.jsx`) but no **curves** — you can't see
  loss/accuracy over steps without the (unreachable) iframe (§3.6, §4.4).
- 🟠 **Stale-flash + sticky-segment bugs** on navigation (§1.8).
- 🟡 **No Reviews segment.** Reviews are folded into Plan/Outcomes spotlights,
  which is defensible, but there's no single place to see the full review
  history the way desktop's stepper implies.
- 🟡 **Terminal lives in nested scroll.** `mobile.css:227` caps `.term-body` at
  `56dvh`, creating a scroll-within-scroll. The plan wanted a full-height
  `100dvh` terminal segment with no nested scroll (`§Phase 2`).

**Proposals:** add a **Graph** segment (or a "Story" card) backed by
`GraphOutline` + a fullscreen ReactFlow overlay (§4.1); add `MetricsChart`
curves to Outcomes (§3.6); make the Run terminal full-bleed; fix §1.4/1.5/1.8.

### 2.5 SandboxCardList — "Compute fleet"  🟠
`mobile/SandboxCardList.jsx`.

**Working well:** running-first sort, burn/expiry meta, parachute chips,
hardware/SSH endpoint, an expandable in-card **drawer** hosting the terminal, and
a genuinely careful **two-step release** with a red running-experiment
escalation. This is good mobile design.

**Edge cases / failures:**
- 🔴 **The drawer hosts `SandboxTerminal`, which re-introduces the unguarded
  release button** (§1.2) directly *below* the careful confirm flow — and the
  iframe tabs (§1.5) and the missing visibility-pause (§1.4).
- 🟠 **"Release…" is a tap-tap confirm, not a slide.** The plan specified
  *slide-to-confirm* (`§2`) precisely because a destructive GPU-kill deserves a
  deliberate gesture, not a second tap that muscle-memory blows through.
- 🟡 **SSH endpoint `user@host:port` and `ssh -i …` command** are shown
  (`SandboxCardList.jsx:122`, `SandboxTerminal.jsx:446`) but you can't actually
  SSH from a phone — and `navigator.clipboard` silently no-ops over plain HTTP
  (works only on the HTTPS tailnet). Low value, occasional dead "copy".
- 🟡 Multiple expandable drawers + the release confirm can make a card very tall;
  no "collapse all" / the terminal keeps polling while scrolled off (§1.4).

**Proposals:** pass `readOnly` to the embedded `SandboxTerminal` (kill its action
header + iframes on mobile); implement an actual **slide-to-confirm** control for
release; demote SSH details behind a disclosure; give clipboard a toast on
success and a graceful "couldn't copy (needs HTTPS)" on failure.

### 2.6 Claims — "What we think" (reused desktop page)  🔴
`pages/Claims.jsx` rendered at `/claims`.

**Edge cases / failures:**
- 🔴 **Exposes claim creation on mobile** (`New claim` → form → `createClaim`,
  §1.2) — violates read-only.
- 🟠 **7-tab `.tab-row` wraps to multiple rows** on a phone
  (`.tab-row { flex-wrap: wrap }`, `global.css:296–300`): all / active /
  supported / weakened / contradicted / draft / abandoned. Visually heavy and
  inconsistent with the scrollable `.mchips` used on the native screens.
- 🟡 `.page-head-row` puts the title block + "New claim" button on one
  non-wrapping flex row (`global.css:226`), compressing the title on narrow
  widths.

**Proposals:** ship a **`MobileClaims`** card screen mirroring
`ExperimentCardList` (scrollable status chips + claim cards with the confidence
dots and linked-experiment glyphs already in `ClaimEntry`), read-only. At minimum
(cheap interim): hide `.page-actions` and convert `.tab-row` → scrollable chips
under `data-surface="mobile"`.

### 2.7 ClaimDetail (reused)  🟠
`pages/ClaimDetail.jsx` at `/claims/:id`.

**Edge cases / failures:**
- 🟢 `KvList` is mobilized (`.kv-row` → `104px 1fr`, `mobile.css:213`) and reads
  fine.
- 🟠 **Raw ISO timestamp** rendered verbatim (`created_at`, `:61`) — desktop has
  hover tooltips for full precision; mobile just shows the long machine string.
- 🟠 Linked-experiment `.list-row` titles hard-ellipsize (`white-space:nowrap`,
  `global.css:1043–1049`) — long intents become "Lorem ipsu…".

**Proposals:** humanize timestamps (reuse `fmtDayTime`/`fmtDuration`); allow the
linked-experiment titles to wrap to two lines on mobile.

### 2.8 Reviews (reused)  🟠
`pages/Reviews.jsx` at `/reviews`.

**Edge cases / failures:**
- 🟢 `ReviewCard` is already card-shaped (per plan) and reads acceptably.
- 🟠 **Open-requests `.list-row` packs title + `<ObjId>` + reason into a
  nowrap row** with a right-aligned pill + "Open →" button
  (`Reviews.jsx:72–88`); on a phone the title gets a sliver of width.
- 🟠 The grouped "Submitted" header uses `.cluster--between` with the experiment
  name + intent + an "Open experiment →" button on one row (`:103–108`) — wraps
  awkwardly.
- 🟡 Reviews is buried in the More sheet despite being the supervisor's core
  "needs attention" surface.

**Proposals:** a thin `MobileReviews` (or mobile overrides) that stacks request
rows into cards with the pill/CTA on their own line; promote Reviews toward a
top-level tab (§2.1).

### 2.9 Events (reused)  🟠
`pages/Events.jsx` at `/events` (this is the "Activity" bottom-tab target).

**Edge cases / failures:**
- 🟢 The `EventTimeline` rows are mobilized (`.timeline-row` → `86px 1fr`,
  `mobile.css:214`) and read well.
- 🟠 **The filter bar is desktop-shaped:** a wrapping `.tab-row` of 5 category
  pills **plus a native `<select>` of every event type** (`Events.jsx:104–124`).
  The select is fine on mobile (native picker), but the category pills wrap and
  the "showing N" count gets lost.
- 🟡 Because the bottom tab labeled **"Activity"** points to `/events`, while the
  More sheet's "Live traffic" points to `/activity`, the naming is ambiguous —
  two different screens both feel like "activity".

**Proposals:** convert the category row to scrollable chips; keep the native
`<select>` (or move type-filter into a bottom sheet); rename for clarity
("Activity" tab → "Log", or relabel the forensic one).

### 2.10 Projects / CreateProject (reused)  🔴
`pages/Projects.jsx` at `/projects`, `pages/CreateProject.jsx` at `/projects/new`
(and as the bootstrap screen).

**Edge cases / failures:**
- 🔴 **CreateProject asks for a server-local absolute directory path**
  (`/absolute/path/to/research-project`, `CreateProject.jsx:73–80`). You cannot
  know or type a host filesystem path from a phone — **project creation is
  effectively impossible on mobile**, yet the form is fully presented (and is the
  *bootstrap* screen if no projects exist, so a first-run-on-phone dead-ends).
- 🔴 **Projects exposes rename (`patchProject`) and create** on mobile (§1.2).
- 🟠 `.page-head-row` with Refresh + "+ New project" + per-card Edit/Switch
  buttons is dense on a phone.

**Proposals:** on mobile, make `/projects` a **read-only switcher** (tap a card to
switch — reuse the sheet's project list); hide Edit/New. For bootstrap-on-phone,
show an honest "create a project from the desktop/CLI, then it appears here"
state instead of a path field you can't fill. (Aligns with the plan's "mobile
supports exactly one daemon / the default project" stance.)

### 2.11 Activity — "Live MCP traffic" (reused)  🔴
`pages/Activity.jsx` at `/activity` (More → "Live traffic").

**Edge cases / failures:**
- 🔴 **The row layout collapses.** `.act-row-main` is a fixed 5-column grid
  `78px 60px 1fr 60px 70px` with 10 px gaps (`global.css:2558–2563`). On a 390 px
  screen that leaves the `1fr` **tool/path column ~50–60 px wide** — the actual
  content (the MCP tool name, the HTTP path) is crushed to an ellipsis while time
  / source / status / duration eat the width.
- 🟠 The 3 filter groups (source/event/status), each a `.tab-row`, plus a
  live/paused counter and Pause/Refresh buttons in `.page-head-row`, wrap into a
  tall, busy header.
- 🟠 Expanded detail renders raw JSON `<pre>` panes (`ActivityDetail`) that scroll
  horizontally — readable but not pleasant.

**Proposals:** a mobile `.act-row` treatment that **stacks** into a card (line 1:
time · source · status · dur; line 2: full tool/path, wrapping); move filters into
a single chip row + a bottom-sheet for the long ones; keep JSON in a horizontally
scrollable mono block (acceptable for a power-user forensic view). If full
mobilization isn't worth it, at least stop the column collapse.

### 2.12 Debug — "Tool I/O" (reused)  🔴 / [plan-known "desktop recommended"]
`pages/Debug.jsx` at `/debug` (More → "Tool I/O", noted "desktop recommended").

**Edge cases / failures:**
- 🔴 **Two fixed-width data tables** overflow the viewport: the by-tool table is
  `min-width: 720px` and the calls table `min-width: 640px`
  (`global.css:3513–3524`), wrapped in `overflow-x:auto`. On a phone this is a
  9-column spreadsheet you sideways-scroll through a porthole; the share bars,
  p95/max columns, and sortable headers are all but unusable.
- 🟠 Exposes **Clear** (destructive, `window.confirm`-gated, §1.2).
- 🟡 The "desktop recommended" note (`MobileShell.jsx:144`) is honest but the page
  still fully renders the broken table rather than a graceful fallback.

**Proposals:** keep Debug desktop-first, but on mobile render a **compact
"top offenders" card list** (tool · calls · total recv · p95, with the share bar
as a full-width row) and tap-through to a per-call detail **bottom sheet** instead
of the grid. Hide Clear. This matches the plan's "node-list fallback" philosophy
for the DAG.

### 2.13 VisualDag — "desktop only" notice  🟢 / [plan-known]
`App.jsx:129` renders a `MobileDagNotice` instead of the 1600×820 hover-only SVG.
Correct call (the plan defers the touch port until the desktop canvas redesign
settles, `MOBILE_PLAN.md §Phase 3 / Later`). **Proposal (optional, was a cut
item):** offer the planned **plain node-list fallback** so the lineage is at least
*readable* on mobile, not just "go to desktop".

---

## 3. Mobile-native UI building blocks to introduce

These are reusable primitives that several of the §2 fixes depend on. All live in
`src/mobile/` + `mobile.css` and touch no desktop code.

### 3.1 Buttons & touch targets
- Current mobile buttons are decent (`.btn` min-height 38, `.btn--sm` 34,
  `mobile.css:208–209`) but **`.tab` is only 7px padding / ~34px**
  (`mobile.css:210`) and file-tree rows needed a manual bump (`:462`). Audit all
  *reused-page* interactive elements (`.tab`, `.dbg-seg-btn`, `.act-row-main`,
  sort headers) to the 44px Apple/Material minimum on the mobile surface.
- Add a **destructive button variant** with a confirm/slide affordance baked in,
  used by release.

### 3.2 `<BottomSheet>` primitive (the backbone)
A single sheet component with: drag-handle grip, one or two snap points,
velocity dismiss, backdrop scrim, focus trap, and **`useScrollLock`** (the
position:fixed body technique the plan names for the iOS scroll-leak). Back the
**More sheet** with it, then reuse for: **logic-graph node detail**, **event/activity
call detail**, **filter/sort pickers**, **sandbox actions**, and the **project
switcher**. This converts today's desktop popovers and inline expands into the
native pattern users expect, and centralizes the iOS scroll-lock fix.

### 3.3 Consistent filter **chips** (retire wrapping `.tab-row` on mobile)
The native screens already use scrollable `.mchips`
(`ExperimentCardList`, `mobile.css:339–370`). Apply the same to Claims, Events,
Activity, Debug filters so the whole app filters the same way.

### 3.4 Pull-to-refresh
A scroll-container hook (`overscroll` + touch delta, or a small lib-free
implementation) that calls `refreshHome()` (already threaded as
`onRefresh`, `App.jsx:74`). It's the instant override that makes the **adaptive
polling** in §1.3 acceptable. Mount on NowScreen, card lists, and detail.

### 3.5 Slide-to-confirm
A reusable slider control for release (and any future sanctioned mutation),
replacing the tap-tap confirm in `SandboxCardList` and matching the plan's intent.

### 3.6 `MetricsChart` — plain-SVG curves (no deps, no iframe)  — [plan §Phase 2]
A ~150-line polyline chart over the **durable** archived metrics endpoint
(`GET /experiments/{id}/results/metrics`, already consumed by
`ResultsMetricsPanel`) plus the live `sandbox/metrics` readout. This is the
mobile answer to "watch loss/accuracy" without the unreachable MLflow/TensorBoard
iframes (§1.5). Drop it into the Outcomes segment beside the value grid.

### 3.7 `GraphOutline` + fullscreen graph overlay  — [plan §Phase 3]
See §4.1 — the DOM-first rendering of figure/logic graphs that makes them work on
touch.

### 3.8 Skeletons, toasts, haptics
Per §1.6 — replace `Loading…` text, give actions feedback, vibrate on
destructive commit.

---

## 4. Desktop features the mobile UI lacks — and how to integrate them

| # | Desktop feature | Where (desktop) | Mobile today | Integration proposal | Effort |
|---|---|---|---|---|---|
| 1 | **Experiment figure** (derived state graph) | `ExperimentGraphs`→`ExperimentFigure` (`ExperimentDetail.jsx:204`) | **absent** | `GraphOutline` default + fullscreen overlay (§4.1) | M |
| 2 | **Logic graph** (agent's story) | `LogicGraph` (same slot) | **absent** | same `GraphOutline`/overlay; node tap → bottom sheet | M |
| 3 | **Project synthesis** | `ProjectSynthesisPanel` (`Home.jsx:91`) | **absent** | synthesis headline on Now + detail sheet (§4.2) | M |
| 4 | **Metric curves** | MLflow/TB iframes in `SandboxTerminal` | final values only | `MetricsChart` over durable endpoint (§3.6) | M |
| 5 | **Transition actions** (submit/ready/complete/abandon) | `GateBanner` buttons (`ExperimentDetail.jsx:177`) | read-only (by design) | keep deferred; see §4.4 | — |
| 6 | **Resource add** (plan/code/report) | `AddResourceToExperiment` | none (agent's job) | intentionally omit | — |
| 7 | **MLflow/TensorBoard dashboards** | `SandboxTerminal` iframe tabs | broken iframes | open-↗ for HTTPS, empty state for loopback (§1.5) | S |
| 8 | **Project lineage DAG** | `VisualDag` (`/visual/dag`) | "desktop only" notice | optional node-list fallback (§2.13) | M |
| 9 | **Rich event/tool forensics** | `Events`/`Activity`/`Debug` tables | desktop layout, cramped/broken | card + bottom-sheet detail (§2.9, §2.11, §2.12) | M |
| 10 | **At-a-glance counts** | `Counts` stat grid (`Home.jsx:115`) | absent on Now | compact counts strip on Now (§2.2) | S |
| 11 | **Deep-link hash scroll** | `useScrollToHash` (`ExperimentDetail.jsx:62`) | n/a (segmented) | map gate→segment instead of hash anchor | S |

### 4.1 Figure + logic graph — the marquee gap (features 1 & 2)
The desktop renders both as ReactFlow canvases that share one slot and **stack
their detail panel *below* the canvas under 900 px** — which the plan itself
diagnosed as "the invisible below-canvas panel" problem on touch
(`MOBILE_PLAN.md §Phase 3`). Don't port the canvas as-is.

**Two-tier plan (from `MOBILE_PLAN.md §Phase 3`, not yet built):**
1. **`GraphOutline` (default, instant):** render the graph as **depth-ordered
   DOM node rows** (the layout data already exists in `utils/figureLayout.js` /
   the graph payload), each row showing kind accent + label + evidence glyph,
   with edges expressed as indentation/connectors. Tapping a node opens a
   **bottom sheet** with its detail and resolved refs (the `NodeRef` resolution
   logic in `LogicGraph.jsx:109–152` is reusable verbatim). No `@xyflow` on the
   critical path → fast, scrollable, accessible.
2. **"View as graph" (opt-in):** lazy-load ReactFlow into a **100dvh fullscreen
   overlay** with touch pan/pinch (the plan's inert-preview recipe: interaction
   props off + `preventScrolling={false}` + a tap-capture overlay). This keeps the
   heavy dep out of first paint and gives the spatial view to those who want it.

Surface it as a new **Graph/Story** segment in `MobileExperimentDetail` (or a card
above Outcomes). This single piece closes the largest perceived gap between the
two surfaces.

### 4.2 Project synthesis (feature 3)
`ProjectSynthesisPanel` (the project's synthesized narrative, desktop Home only)
has no mobile entry point. Propose: a **one-line synthesis headline card** on
NowScreen ("Synthesis · <wave status> — tap to read") opening a detail
**bottom sheet** that renders the synthesis body (it can reuse the same
`LogicGraph` component in its project-graph mode — note `LogicGraph` already
accepts a `fetcher`/`live` override for exactly this reuse,
`LogicGraph.jsx:194–199`). Also add it to the More sheet under "Browse".

### 4.3 Metric curves (feature 4) — see §3.6.

### 4.4 Transition actions (feature 5) — deliberate gap, with a future path
Mobile is read-only by design, and the figure/graph/metrics work above is higher
value than mutation. But the away-from-desk supervisor's *next* most-wanted action
after "release a burning VM" is plausibly **approving a design/experiment gate**.
Recommendation: **keep deferred for now**, but when revisited, gate it exactly
like release — a single curated action (the `next_action` the server already
advertises in `workflow`, `GateBanner.jsx:31`) behind slide-to-confirm, never the
full secondary set (abandon/mark-failed stay desktop-only). Record the decision in
`FRONTEND_REDESIGN_OBJECTIVES.md` alongside the "release is the only mobile
mutation" note.

---

## 5. Prioritized roadmap

**P0 — correctness & safety (small, high-impact):**
1. Pass `readOnly` to embedded `SandboxTerminal`: hide its release button + the
   MLflow/TensorBoard iframe tabs on mobile (§1.2, §1.5). *Closes a destructive
   mis-tap and the dead-iframe trap.*
2. Add `visibilitychange` pause to `SandboxTerminal`'s two pollers (§1.4).
   *Battery + a live desktop bug.*
3. Hide mutation affordances on reused pages on mobile: Claims "New claim",
   Projects edit/new, Debug "Clear" (§1.2).
4. Reset `statusData`/`segment` on `experimentId` change (§1.8).

**P1 — make the depth screens mobile-native:**
5. `<BottomSheet>` primitive + back the More sheet with it (§3.2).
6. Pull-to-refresh + adaptive polling (§1.3, §3.4).
7. `GraphOutline` + fullscreen overlay → new Graph segment (§4.1). *The marquee
   gap.*
8. `MetricsChart` curves in Outcomes (§3.6, §4.4).
9. Mobilize Activity row collapse + Debug → card/sheet fallback (§2.11, §2.12).

**P2 — polish & parity:**
10. `MobileClaims` / `MobileReviews` read-only card screens; promote Reviews in
    the nav (§2.6, §2.8, §2.1).
11. Synthesis headline + sheet on Now (§4.2); counts strip (§2.2).
12. Slide-to-confirm, skeletons, toasts, haptics, SVG nav icons (§3.5, §3.8, §2.1).
13. Scrollable-chips everywhere; humanized timestamps; wrap-friendly titles
    (§3.3, §2.7).
14. Bootstrap-on-phone honest state; VisualDag node-list fallback (§2.10, §2.13).
15. PWA manifest / apple-touch-icon / service worker (plan §Phase 3).

**Guardrails (unchanged from the plan):** every change stays in `src/mobile/` +
`mobile.css` (scoped under `html[data-surface="mobile"]`); no edits to desktop
pages the redesign will replace; the §1.4 terminal fix is the one change that
also lands on desktop (it's a shared-component bug).

# Architecture Review: Separation · Extendability · Leanness · Host Symmetry · OSS-Reuse

> **Pass 15 — ~97% complete.** The control/data/MCP migration has effectively landed. This is a clean current-state snapshot; the 14-pass delta history lives in the commit log. The `http_api` god-file — the standing #1/#2 hotspot for most of the review — was dismantled this period, and the last ratchet gap (transport raw-SQL) is now CI-enforced.
>
> **Net: tightening. No structural debt outstanding.** What remains is one one-line wiring decision + an optional decomposition + three low cosmetics.

**Bottom line:** A healthy, well-separated, lean codebase that has executed its own review recommendations pass-over-pass and converged. Every big structural question is resolved: the record-only `ControlApp` seam, config-only host symmetry (mounted key + durable-config fail-fast + in-memory control sinks), a transport rim with **zero raw persistence now enforced by a blanket AST lint**, disciplined build-vs-buy, and twin-drift systematically deleted and pinned. The remaining ~3% is finish-work, not architecture.

## Update — pass 16 (~99%, verified directly)

Six commits closed essentially the entire pass-15 remaining list:
- **`sandboxes.py` decomposed (1607 → 1301 L)** — `3be22dc`/`2cc5845` extracted `SandboxParachute` (165 L) and `SandboxMetrics` (237 L) as neutral, port-mediated collaborators; `sandboxes.py` is now a **facade that delegates** (`self.metrics`/`self.parachute` + injected method refs), pinned by `test_sandbox_decomposition.py`. The last A− (leanness) holdout — the one undecomposed god-file — is **resolved**. → **Leanness now A.**
- **Transport holds zero raw-store touches** — `e20889b` moved the lone `store.transaction()` (`require_project_scope`) into a service; `http_api` `store.transaction` count = 0.
- **`f1e5c22`** replaced the two `sandbox.release/get` literal-name branches with contract capability flags (derive-from-the-table complete); **`acc9c80`** routed modal config onto the shared `env.py` (`_env_int` residual gone).
- **`2c5177c`** made the HTTP surface policy **injectable** (`surface_policy or HttpSurfacePolicy.for_surface(...)`) — so the auth-surface concerns can now be overridden with an independent policy object; the *default* still derives from `auth is None`, so wiring it to config is the lone remaining item.

**Net: grades now A / A / A / A / A. Estimated ~99%.** The only remaining item with any substance is wiring the (now-injectable) auth-surface policy to config inputs by default — a one-liner. Everything else is converged and lint-pinned.

## Scorecard — ~99%

| Axis | Grade | One-line |
|---|---|---|
| **Separation** | A | Layers clean; transport holds zero raw persistence (blanket-lint-enforced); the one residual is the `auth_required` driver being single-sourced (named-but-coupled, below). |
| **Extendability** | A | Derive-from-one-table everywhere (`TOOL_CONTRACTS`, `GATE_TABLE`, `GRAPH_REF_TYPES`, review tables, now `HOSTED_CONTROL_TOOL_POLICIES`/data-plane capability tables); ports earn keep. |
| **Leanness** | A | Twins deleted + pinned; `sandboxes.py` decomposed into a facade + `SandboxParachute`/`SandboxMetrics` collaborators (1607 → 1301 L). No god-files of concern remain. |
| **Host symmetry** | A | Same binary localhost vs cloud, config-only; control disk-clean by ratchet (mounted key required, `LocalMgmtKeyStore` lint-banned from control). |
| **OSS-reuse** | A | OSS behind ports where it fits; managed key store landed by mounting the orchestrator's secret (no vendor SDK); wins are stdlib consolidations. |

## Resolved this period (the `http_api` dismantling + ratchets)

| Item | Status | Detail |
|---|---|---|
| **`http_api` decomposition** | ✅ **Resolved** | Genuine, not shuffling. **2021 → 1710 L.** `admin_http.py` (33 L, business-logic-free route module taking a *narrow* `store` handle, not the app) and `http_policy.py` (70 L, stdlib-only `dataclasses` — the surface-policy in one place) extracted. Real logic left into thin modules. |
| **Transport raw-persistence ratchet** | ✅ **Resolved (the last ratchet gap)** | `test_http_transport_does_not_own_raw_persistence` is a genuine **blanket AST ban** — walks every node, regexes all string/f-string constants for SQL verbs, flags every `.execute()`/`.connect()` by attribute, *no allowlist*. A `SELECT`/`store.connect` in any new or renamed method now fails CI. Confirmed: `http_api` has 0 raw SQL / 0 `store.connect`. |
| **Data-plane capabilities table** | ✅ **Resolved** | The per-tool authz cascade is gone — `HOSTED_CONTROL_TOOL_POLICIES` + `HTTP_DATA_PLANE_FEATURE_TO_TOOL` registry tables drive it; **zero `elif name ==` chains** remain. |
| **Project-scope centralization** | ✅ **Resolved** | The 5× `require_project_id` twin → one `require_project_scope()` helper (`.require_project_id(` appears exactly once now), lint-pinned against re-expansion. |
| **`auth_required` split** | 🟠 **Named, one line from done** | *Consumer* side fully split: the `auth_required` token is gone (lint-banned), 28 distinct `surface.*` reads — CORS reads `restrict_cors`, bearer reads `require_bearer_auth`, data-plane reads its own flags. *Producer* side still single-sources all of them from one `auth is None` sentinel, so they're not yet independently configurable. The seam exists; wiring it is a one-liner. |
| Cosmetics | ✅ **Resolved** | Lone `:797` `store.connect` moved into a synthesis-service method (mirrors its sibling); empty-CORS WARN; loopback token via `mint_secret`. |

## Remaining (~3% — none structural)

| # | Item | Note | Sev |
|---|---|---|---|
| 1 | **Wire the auth-surface concerns to independent config** | One line at the `for_surface` construction site (`http_api.py:884-889`) — pass differing values from config instead of collapsing to `auth is None`. *Delivers* the dev-relaxed-auth-on-control use case the seam already enables. | low-medium |
| 2 | `services/sandboxes.py` (1607 L) | Now the single largest file. **Not a dumping ground** — heavy machinery already delegated to siblings/ports; residual is coordination glue. Decompose the metrics-coordination + parachute clusters *or* consciously accept it as a cohesive orchestrator. Low urgency. | leanness · low |
| 3 | Lone `store.transaction()` in transport (`:921`) | The `require_project_scope` helper opens a raw `store.transaction()` (a delegated authz check, no SQL) — the ratchet bans `.connect`/`.execute` but not `.transaction`. Move into a service method, or extend the lint. | low |
| 4 | Two `name == "sandbox.release/get"` branches | The last transport branches keyed on a literal tool name (lifecycle specializations, not authz) — replace with a contract capability flag to complete derive-from-the-table. Plus the `modal/config.py` `_env_int` residual not yet on `env.py`. | low |

## Prioritized improvements

1. **(S, low-med) Wire the 3 auth-surface concerns to independent config inputs** — the 10-field policy + `for_surface` seam already exists; passing config-driven values delivers independently-configurable bearer-auth / CORS / data-plane exposure (and dev-relaxed-auth on control). The only remaining item with user-facing value.
2. **(S, low) Eliminate the lone raw `store.transaction()` in transport** — move the `require_project_scope` authz block into a service method (transport then holds *zero* raw-store touches) or extend the ratchet to ban `.transaction`.
3. **(S, low) Replace the `sandbox.release/get` literal-name branches with contract capability flags** — completes the derive-from-the-contract-table pattern for the data-plane surface.
4. **(M, low) Decide `sandboxes.py`** — split the metrics/parachute clusters *or* accept it; either is fine since the machinery is already delegated. This is the only path to "zero god-files," but it's a preference, not a defect.

## What the architecture gets right (durable wins)

- **The ControlApp seam is the keystone** — record-only composition via shared `build_record_core`, neutral in-memory control sinks, injected `MgmtKeyStore`; resolved separation, extendability, leanness, *and* host symmetry at once.
- **Ratchets, not conventions** — every win is now pinned by a lint (the blanket transport AST ban, the percentile parity test, the gate-role equivalence lint, the project-scope pin, the `LocalMgmtKeyStore` ban). This is why 15 passes converged instead of drifting, and why "97%" is durable rather than momentary.
- **Derive-from-one-table is the universal idiom** — tools, gates, graph-refs, review roles, and now HTTP surface/data-plane capabilities all extend by adding a row.
- **Build-vs-buy is mature** — OSS behind ports where it fits, deliberate stdlib-only where packaging demands, managed keys by mounting a secret not pulling an SDK.
- **The thin channels hold** — `SandboxBackend`, `TOOL_CONTRACTS`, `GATE_TABLE`, storage Protocols, `task_channel`, the neutral `Connection`/`Row` protocol, and now `http_policy`/`admin_http` as thin transport modules.

**The through-line:** the project set out to split a local-only tool into a clean control/data/MCP architecture with thin channels and no highly-connected components — and across all five axes it has **arrived (~97%)**. The last structural concern (the `http_api` god-file) was dismantled this period, and the final ratchet gap is closed, so the lints now hold essentially every invariant. The remaining ~3% is one one-line config wiring, one optional decomposition, and three cosmetics — genuinely finish-work. **Recommendation: dial the review loop back from hourly** (the structural questions are answered and CI guards the rest); a daily or on-demand cadence is now sufficient.

---

# Rubric-Based Review — 2026-06-21 (agent-team pass)

> Independent adversarial review against the **5-point code-quality rubric** (modularity · separation · brevity · one-liner comments · OSS-reuse). A 23-agent workflow — 3 mappers + 9 finders (dimension × subsystem) + 9 hostile verifiers — surfaced **33 findings that survived verification against source**. This is a deliberately harder read than the loop snapshot above, and it **partly contradicts it** (see Divergence).

## Rubric scorecard

| # | Rubric dimension | Score | maj/min/nit | Verdict |
|---|---|---|---|---|
| 1 | Modular architecture | **3 / 5 (C+)** | 7 / 2 / 0 | Solid module skeleton (services/ports/composition), undermined by 4 god-modules + verbatim desktop↔mobile duplication. |
| 2 | Separation of concerns | **3 / 5 (C+)** | 7 / 2 / 0 | Layering mostly holds, but transport reaches into raw `store`, the App facade is wide, and mobile depends on the whole desktop surface. |
| 3 | Code brevity | **4 / 5 (B+)** | 0 / 5 / 3 | Largely lean; residue is dead code, pass-through wrappers, duplicated formatters. |
| 4 | Comments are one-liners | **4 / 5 (B+)** | 0 / 1 / 4 | Rule largely followed; a few multi-line design-memos + decorative banners. |
| 5 | Reuse open source | **4 / 5 (B+)** | 2 / 0 / 0 | Good build-vs-buy overall; two reinvented commodity wheels (ANSI parser, DAG layout). |
| | **Overall** | **3.5 / 5 (B−)** | **16 / 10 / 7** | Strong skeleton with real structural debt concentrated in `http_api`, the desktop/mobile split, and a few god-files. |

## Divergence from the loop snapshot above

- The loop reports `http_api` **"dismantled … resolved" (Separation A)**. Reading current source, the rubric team finds `http_api.py` **still 1748 lines — one `ResearchHttpApi` class, 117 defs, fanning out to 12 backend surfaces** (MOD-1, SOC-4). The `admin_http`/`http_policy` extractions are real, but the core class is still a god-file by the rubric's *one module → one responsibility → testable in isolation* bar.
- The loop's transport ratchet bans **raw SQL / `.execute` / `.connect`**. The rubric caught `http_api.py:323` calling `app.store.recent_events(...)` and `:137` reading `app.store.db_path` (SOC-2) — *method* calls into the store that pass the lint yet still skip the service layer. **A separation gap the existing lint does not cover.**

## 1 · Modular architecture — 3/5

| ID | Sev | Where | Problem → Fix |
|---|---|---|---|
| MOD-1 | major | `http_api.py:97,867` | 1748-line file = view class (~770 L) + 880-line app factory (~60 route handlers, 2 middlewares, CORS, inline tool-call policy). → Split into view / routes / tool_call_router / http_middleware. |
| MOD-2 | major | `services/syntheses.py:743-1121` | ~380-line change-spec DSL (parse/validate/materialize) buried in a 1421-line, 46-method service. → Extract `services/change_spec.py`. |
| MOD-3 | major | `http_api.py:390-822` | Resource-content + graph-projection domain policy in the HTTP view class, against its own "domain logic stays in services" docstring. → Relocate to `ResourceService` (absorbed by MOD-1). |
| MOD-4 | minor | `http_api.py`, `daemon_http.py` | principal/tenant `getattr` boilerplate open-coded 11×/9×/3×. → `principal_of(request)` / `tenant_of(principal)` in `http_policy.py`. |
| MA-1 | major | `Claims.jsx:150` / `MobileClaims.jsx:10` | SUPPORT/AGAINST status sets + `categorize()` copied verbatim desktop↔mobile — already drifting (mobile lacks the LIVE branch). → Shared `utils/claimStatus.js`. |
| MA-2 | major | `ExperimentFigure.jsx:13` / `mobile/graphModel.jsx:11` | status→class + glyph maps forked (code self-admits "mirrors"). → Shared `utils/figureModel.js`. |
| MA-3 | major | `pages/VisualDag.jsx` (754 L) | God-page: hover/focus state + 217-line SVG node switch + TimeAxis + Legend + date helpers. → Per-kind node components, TimeAxis, HoverInfo, Legend. |
| MA-4 | major | `components/SandboxTerminal.jsx` (648 L) | Two pollers + transcript + tab/iframe + fullscreen + SSH meta + usage gauges in one component. → Extract panels; `useSandboxTranscript` hook. |
| MA-5 | minor | 8 files | Byte/number/date formatters re-implemented across 3+ files (short-date in 4); `utils/format.js` already exists. → Add `fmtNum` + `shortDate`, dedupe. |

## 2 · Separation of concerns — 3/5

| ID | Sev | Where | Problem → Fix |
|---|---|---|---|
| SOC-1 | major | `app.py:65-98` | `ResearchPluginApp` exposes 27 public attrs flat — raw infra (`store`, `blobs`, `worker`, `execution_backend`) beside domain services; the bridge hides nothing. → Make infra private/bundled; expose only domain services. |
| SOC-2 | major | `http_api.py:323,137` | **Strongest finding.** HTTP calls `app.store.recent_events()` and reads `app.store.db_path` directly, bypassing services. → Add service methods; remove the `app.store` reach. |
| SOC-3 | major | `services/sandboxes.py:164-242` | `SandboxService` constructs 6 sub-services itself + calls `daemons.start()` — buried composition root, built at 2 roots. → Move wiring to `composition/`, inject collaborators. |
| SOC-4 | major | `http_api.py:97` | One class, 117 defs, fan-out to 12 backend surfaces ≈ the whole backend. → Per-resource routers, each depending on 1–2 services. |
| fe-soc-1 | major | `mobile/*` → `components/*` | No `shared/` dir; 9 mobile files import 12 desktop components — an implicit wide cross-surface bridge. → Create `shared/`, move cross-surface leaves, lint-forbid `mobile/`→`components/`. |
| fe-soc-2 | major | `mobile/MobileProjectCreateNotice.jsx:2` | Mobile imports a desktop route page (`pages/CreateProject`) — dependency points up at the most volatile layer. → Extract `shared/CreateProjectForm`; mobile never imports `pages/`. |
| fe-soc-4 | major | `utils/graph.js` + `mobile/graphModel.jsx` | Graph model forked into two parallel impls, hand-synced. → Shared surface-agnostic `graphDomain` module. |
| fe-soc-5 | minor | `components/SandboxTerminal.jsx` | Cross-surface god-component with embedded data-plane pollers used as a shared leaf. → `useSandboxTranscript` hook with a narrow contract. |
| fe-soc-6 | minor | `LogicGraph.jsx:6`, `ProjectSynthesisPanel.jsx:6` | Siblings import each other's internal helpers (`MeasureSync`, `SYNTHESIS_*`). → Move to neutral shared modules. |

## 3 · Code brevity — 4/5

| ID | Sev | Where | Problem → Fix |
|---|---|---|---|
| BREV-B1 | minor | `services/sandboxes.py:1113` | Dead `_pulled_mlflow_db_path` wrapper, referenced only by a test. → Delete; test calls the worker method. |
| BREV-B2 | nit | `http_api.py:190` | `tool_call_stats` (and `tool_calls_clear`) is a pure pass-through. → Drop; route calls `app.tool_calls.*` directly. |
| BREV-B3 | minor | `http_api.py:1442,1486` | `project_status` / `experiment_status` handlers byte-identical incl. comment. → Extract `_status()` helper. |
| BREV-B4 | nit | `services/sandboxes.py:1072,1119` | Single-callsite pure forwarders. → Inline (confirm `_ensure_keypair` isn't a doc'd plane seam first). |
| BREV-F1 | minor | `ResultsMetricsPanel.jsx:6` / `MobileMetricsPanel.jsx:6` | `fmtNum` duplicated byte-for-byte. → Export from `utils/format.js`. |
| BREV-F2 | minor | `components/Sidebar.jsx:10` | Reimplements the `fmtAgo` ladder already in `format.js`. → Use `fmtAgo`. |
| BREV-F3 | minor | `SandboxTable.jsx:89` / `SandboxCardList.jsx:97` | Hardware-summary + ssh-endpoint strings duplicated. → Shared `sandboxHardware()`/`sshEndpoint()`. |
| BREV-F4 | nit | `pages/Debug.jsx:211` | Dead `p50_received_chars` computed every pass, never rendered. → Drop. |

## 4 · Comments are one-liners — 4/5

| ID | Sev | Where | Problem → Fix |
|---|---|---|---|
| comments-02 | nit | `services/experiment_views.py:8-18` | 11-line design-memo on a 4-tuple; rationale belongs in the linked doc. → One line + doc link. |
| comments-03 | nit | `services/workflow_views.py:14-23` | 10-line design-memo, same pattern. → One line + doc link. |
| fe-comments-1 | minor | `pages/ExperimentDetail.jsx:168-270` | Box-drawing ALL-CAPS section banners; labels restate the JSX below. → Strip rules/labels, keep the one-line why. |
| fe-comments-3 | nit | `api.js:90-189` | One-word group labels (`// Projects`, `// Claims`…) restate the methods. → Delete. |
| fe-comments-4 | nit | `ExperimentFigure.jsx:192` | Orphan "(Phase 0)" — the only Phase ref in the UI; will go stale. → Drop. |

## 5 · Reuse open source — 4/5

| ID | Sev | Where | Problem → Fix |
|---|---|---|---|
| OSS-1 | major | `components/TerminalLog.jsx:30-132` | ~60-line hand-rolled ANSI/SGR parser + xterm-256 cube + `\r` emulation; silently drops non-SGR CSI. → Adopt `anser`/`ansi-to-html`; keep rec.sh segmentation + StatusBar in-house. |
| OSS-2 | major | `utils/figureLayout.js:24-86` | Hand-written DAG layout (Kahn topo-sort + longest-path layering) while `@xyflow/react` is already a dep. → Use `dagre`/`elkjs` for layering; keep the time-axis X (`created_at`) in-house. |

## Prioritized fixes (highest leverage first)

1. **(L) Split `http_api.py`** — MOD-1 / MOD-3 / SOC-4 all converge here; the single biggest lever across modularity *and* separation.
2. **(M) Stop transport reaching into `state.store`; narrow the App facade** — SOC-2 / SOC-1; closes the gap the raw-SQL lint misses (store *method* calls).
3. **(M) Introduce a frontend `shared/` boundary + `graphDomain` module** — retires fe-soc-1/2/4 + MA-1/2/5 at once; lint-forbid `mobile/`→`components/` and `mobile/`→`pages/`.
4. **(M) Extract the change-spec DSL from `syntheses.py`** — MOD-2; sheds ~380 L and drops the service below the god-threshold.
5. **(M) Decompose `VisualDag` + `SandboxTerminal`; lift a `useSandboxTranscript` hook** — MA-3 / MA-4 / fe-soc-5.
6. **(M) Move `SandboxService` wiring into `composition/`, inject collaborators** — SOC-3.
7. **(S–M) Adopt `anser` + `dagre`/`elkjs`** — OSS-1 / OSS-2; deletes ~60 L of escape parsing and the layout math.
8. **(S) Low-risk lean/comment sweep** — dead code (BREV-B1, F4), pass-through wrappers (BREV-B2/B4, B3), formatter dedupe (BREV-F1/F2/F3, MA-5), accessors (MOD-4), strip banners/labels (fe-comments-*), trim memos (comments-02/03).

## Method / caveat

33 findings from a 23-agent workflow (3 map → 9 find → 9 verify), each hostile-verified against source before being kept. The **completeness-critic and an independent scoring pass were lost to a transient server rate-limit**, so this scorecard was synthesized by hand from the 33 verified findings and has *not* had a second "what did we miss" sweep — coverage is high-precision but not guaranteed-complete. Scores weight major ≫ minor ≫ nit; god-files cap modularity/separation at "mixed" rather than "fails."

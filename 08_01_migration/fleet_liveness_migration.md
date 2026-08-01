# Sandbox fleet liveness — session log + production migration plan

Date: 2026-08-01 · Session scope: making a whole compute fleet observable at
once (Sandboxes → Active) · Status: **built + locally verified, NOT committed,
NOT Codex-reviewed, NOT deployed**

---

## 1. The problem, and why the fix was cheap

The Sandboxes console could only show details for one box at a time. The
apparent cause was a single-value `expanded` state in `SandboxTable`; the real
cause was that **the only instrument was the most expensive one**. Learning
anything live about a box required mounting a full `SandboxTerminal`, which
independently polls `/sandbox` (3s), `/metrics` (3s, SSH exec), and `/terminal`
(1.5s, SSH read). Stacking those was deliberately prevented — the comment in
`mobile/SandboxCardList.jsx` said so outright.

Meanwhile the table row carried only provisioning facts (GPU model, uptime, SSH
endpoint) that were true at creation and never change. Nothing on the row
answered *is this thing working?*

So the fix was not "let more drawers open". It was the missing **overview
tier** — Shneiderman's "overview first, zoom and filter, details on demand".
Reference points that shaped it: Datadog Host Maps (per-host state as one
colour channel, so 10 or 10,000 hosts scan the same way), k9s multi-pod log
tail (multi-watch is a deliberate *mode*, not the browsing default), and the
sparkline-metrics-table pattern (value + direction + compressed trend per row).

**The unlock: the data was already being produced and thrown away.**

- `SandboxScheduler._loop` sweeps every `DEFAULT_REAPER_INTERVAL_SECONDS = 30`,
  and `reap_idle → _tick_row` already called `sample_metrics()` for **every
  running row** to make idle decisions.
- The `sandboxes` table already stored `last_command_text`, `last_command_status`,
  `last_command_exit_code`, `last_command_started_at`, `last_command_finished_at`.
- `/sandboxes` was already ETag-gated on `project_sandbox_signal`, which already
  digests `updated_at` **and** `last_command_snapshot_at` — so it already
  invalidated exactly when this payload would.

`_canonical_snapshot` simply dropped all of it. Net: whole-fleet liveness costs
**zero new SSH connections and zero new HTTP requests** — it is a projection
change over work the control plane was already doing.

## 2. What this session built

### Tier 1 — every row carries its own liveness

Each row grew a second line: **state · current command · elapsed-or-exit**, a
utilization strip (GPU / VRAM / RAM, or CPU / RAM on a CPU-only box), and a
trend sparkline. Status colour follows *behaviour*, not lifecycle:

| tone | meaning | precedence |
|---|---|---|
| `work` (green) | a command is in flight | 1 |
| `fail` (red) | last command exited non-zero | 2 |
| `idle` (amber) | `idle_since` set — **money burning** | 3 |
| `quiet` (grey) | done / nothing run yet / terminated verdict | 4 |

The amber state is the most valuable and was entirely invisible before: an idle
H100 whose job exited twenty minutes ago. `idle_since` already knew.

### Tier 1b — a real trend, correctly scaled

`heartbeat_snapshot_json` grew a bounded `series` ring (`HEARTBEAT_SERIES_MAX =
30`; at the 30s sweep ≈ 15 minutes, inside the readable 7–30-point range).
`Sparkline` gained a `domain` prop and utilization pins it to `[0, 100]` — the
component's default per-series normalization is right for a loss curve and
actively wrong here, since a box idling between 0 and 3% would otherwise draw
the same dramatic sawtooth as one swinging the full range.

### Sampling decoupled from reaping

The sweep is now the fleet's sampler as well as the reaper. Three coupling
points existed; the third — the one that looked expensive — was already solved:

1. `reap_idle` early-returned at `threshold_seconds <= 0`. **Removed.**
2. `SandboxScheduler._daemon_enabled()` required a reaper to be on. **Added a
   third clause** plus `_sampling_enabled()` (`MERV_SANDBOX_ACTIVITY_SAMPLING`,
   default **true** — opt-out, not opt-in).
3. `_tick_row` mixes sampling and reaping — but every destructive step already
   sat behind `policy.should_reap`, whose first condition is
   `threshold_seconds > 0`. At threshold 0 it samples, records, computes
   `idle_since`, and returns without touching anything. **No restructuring.**

Verified that `idle_since` has exactly one behavioural consumer outside
`heartbeat.py` — the fleet row's own "idle 22m" readout. Nothing destructive
keys on it, so "observe idleness without acting on it" is a coherent state.

### Forward-compatibility guard (deploy-ordering safety)

The UI ships via Vercel from `main`; the backend ships separately to the VM. If
the UI lands first, `last_command` and `heartbeat` are **absent** from every
row — and a naive read would render "no commands yet" against a box that is
busy training. `fleetActivity` now returns `null` when both keys are absent
(absent ≠ empty; the new projection always sends `heartbeat` for a running row,
even as `null`), so the row degrades to exactly today's UI. **Verified against
a simulated pre-projection backend** — zero live lines, no false claims.

### File manifest (this session's fleet work only)

New:
- `research_state_ui/src/utils/fleet.js` — `fleetActivity` / `usageBars` /
  `usageTrend`. Pure, dependency-light, shared by desktop and mobile.
- `research_state_ui/src/utils/fleet.test.js` — 10 tests (`node --test`).

Modified backend:
- `sandbox/heartbeat.py` — `usage_point()`, `append_usage_point()`,
  `HEARTBEAT_SERIES_MAX`; `_snapshot` records the ring; `_tick_row` carries it
  forward; `reap_idle` decoupled from the threshold.
- `sandbox/core.py` — `LIVE_COMMAND_MAX_CHARS = 300`, `_live_snapshot()`,
  `_heartbeat_view()`, wired into **`for_project` only**.
- `sandbox/scheduler.py` — `_sampling_enabled()`, `_daemon_enabled()` clause.
- `sandbox/sandbox.md` — main-flow step 5 + file-map entry (81/100 lines).
- `tests/sandbox/test_sandbox_heartbeat.py` — 23 → 48 tests.

Modified UI:
- `components/SandboxTable.jsx` — liveness line; click target moved to a new
  `.sbxt-rowhead` wrapper so both lines toggle the drawer.
- `components/Sparkline.jsx` — `domain` prop (previously unused component).
- `mobile/SandboxCardList.jsx` — the same rules, stacked for a narrow column.
- `styles/global.css` (`sbxt-live*`, `sbxt-gauge*`, `sbxt-rowhead`),
  `styles/mobile.css` (`msbx-live*`).

> **Shared-tree warning:** the working tree also carries other sessions'
> in-flight work (provider Configure, agent-sessions, Settings/Sidebar UI).
> `global.css`, `sandbox.md`, `core.py`, and `Sandboxes.jsx` contain interleaved
> edits from more than one stream. Assemble commits per-feature (`git add -p` /
> per-file) and **never `git add -A`** (standing rule from the expmap incident).
> This feature does not depend on the provider-Configure work, and vice versa.

## 3. Data model and API contract

### There is no migration

This is the headline difference from the provider-config change set. The series
lives **inside the existing `heartbeat_snapshot_json` TEXT column**. No new
table, no new column, no backfill, no ladder-head bump. Prod stays at whatever
migration the provider work leaves it on.

```jsonc
// heartbeat_snapshot_json — the `series` key is new; everything else unchanged
{
  "sampled_at": "2026-08-01T12:00:00Z",
  "metrics":  { /* raw sample, unchanged */ },
  "previous_metrics": { /* unchanged */ },
  "previous_sampled_at": "2026-08-01T11:59:30Z",
  "series": [ { "at": "…", "cpu": 50.0, "mem": 40.0, "gpu": 94.0, "vram": 60.5 } ]
}
```

Percentages, not raw gauges, so one row's trend is comparable to the next.
Falls back to the row's reserved `cpu` / `memory` when the in-container cgroup
limit is unreadable. **An unknown ratio stays `null`, never 0** — a blank bar is
honest; a zero bar reads as an idle box and could talk someone into releasing
live work.

### Response shape (added to `GET /api/projects/{id}/sandboxes` only)

```jsonc
{
  "last_command": {            // present when a command snapshot exists
    "command": "python train.py …",   // truncated to 300 chars
    "status": "running",
    "started_at": "…", "finished_at": null, "exit_code": null
  },
  "heartbeat": {               // present for RUNNING rows only (may be null)
    "sampled_at": "…",
    "idle_since": null,
    "latest": { "at": "…", "cpu": 42.1, "mem": 38.4, "gpu": 94.0, "vram": 61.5 },
    "series": [ /* ≤30 points */ ]
  }
}
```

Deliberately **not** added to `_canonical_snapshot`: agent/MCP views, the
single-sandbox `/sandbox` endpoint, and every mutation return keep the narrower
shape. `output_tail` is excluded — that belongs to the terminal endpoint, not a
3s fleet poll.

### Compatibility matrix

| | old UI | new UI |
|---|---|---|
| **old backend** | today's behaviour | live line suppressed → today's behaviour ✔ |
| **new backend** | extra keys ignored ✔ | full liveness ✔ |

All four cells are safe, so **deploy order does not matter**. That is a
deliberate property, not luck — see the forward-compat guard above.

## 4. Production migration plan

Prod today: Azure VM (`ssh azureuser@20.98.245.95`, checkout `~/Research-Suite`,
ops dir `~/research-suite-vm`), Postgres record store, UI deployed by Vercel
from `main` (~15 min).

### Step 0 — gates

1. **Codex review** (standing law). Suggested scopes: (a) the sampling/reaping
   decoupling — it changes when the control plane touches customer boxes;
   (b) the projection + payload growth; (c) UI.
2. Assemble commits per-feature out of the shared tree (see warning above).
3. Suites green. At time of writing: `tests/sandbox` + `tests/surface` =
   **990 passed**; `fleet.test.js` = 10 passed; `vite build` clean. One
   unrelated pre-existing failure at HEAD: `research_core.md` is 101 lines and
   trips `tests/structure/test_research_core_documentation.py` (≤100).

### Step 1 — pre-deploy check (do this first; it is the only real behaviour risk)

Confirm what the VM has set for idle reaping:

```bash
ssh azureuser@20.98.245.95 'docker exec -i $(docker ps -qf name=merv) env | grep -E "MERV_SANDBOX_IDLE_SECONDS|RESEARCH_PLUGIN_SANDBOX_IDLE_SECONDS|RESEARCH_PLUGIN_SANDBOX_REAPER"'
```

- **Unset (expected)** → idle reaping is on at the 3600s default, so the sweep
  already samples every running box every 30s. This deploy changes *nothing*
  about when the control plane touches boxes. Proceed.
- **`MERV_SANDBOX_IDLE_SECONDS=""`** → reaping is off today, and this deploy
  would **newly start** SSH sampling of running boxes. That is the intended
  feature, but it must be a decision, not a surprise. Either accept it, or set
  `MERV_SANDBOX_ACTIVITY_SAMPLING=false` in `provider-secrets.env` before
  deploying and accept that fleet rows will show command state without gauges.

### Step 2 — deploy (standard path, no special ceremony)

`pg_dump` → fast-forward pull → `control-up.sh`. No migration to verify. UI
follows on its own via Vercel from `main`; either order is safe.

### Step 3 — verify on prod

1. `GET /api/projects/<pid>/sandboxes` on a project with a running box →
   `heartbeat.latest` populated, `last_command` present.
2. **Bars appear immediately; the sparkline takes ~60s.** Existing rows have a
   `heartbeat_snapshot_json` with no `series` key, and `latest` is derived from
   the stored sample rather than the ring's tail precisely so the gauges do not
   wait. `series` fills one point per 30s sweep; `Sparkline` needs ≥2 points.
   An empty sparkline in the first minute is expected, not a fault.
3. Sandboxes page: rows show state + command + gauges; utilization columns align
   down the table; clicking a row still opens the terminal drawer.
4. Watch for an idle box turning amber — that is the feature earning its keep.

### Rollback

Revert the commits and redeploy. Nothing to undo in the database: the extra
`series` key inside `heartbeat_snapshot_json` is ignored by the old reader
(`heartbeat_snapshot` just parses the blob and hands back the dict), and the old
`_canonical_snapshot` never looks at it. No migration means no down-migration.

## 5. Known characteristics and open items

**Payload growth.** Each running row adds ~2.6 KB (measured): ~13 KB for a
5-box fleet, ~52 KB at 20. The `/sandboxes` ETag already churned every 30s per
running box (heartbeat writes bumped `updated_at` before this change), so what
changed is body size on a 200, not 304-hit rate. Fine at current fleet sizes;
if a fleet ever runs large, the cheap trims in order are: drop `at` from series
points (~32%, the UI never reads it), send only the plotted metric, or move the
series to its own endpoint. **Flagged, not yet needed.**

**Sampling can be switched off.** `MERV_SANDBOX_ACTIVITY_SAMPLING=false`
disables the daemon when nothing else needs it. Rows then degrade to state +
command with no gauges — clean degradation, tested.

**The two tiers have different truth ages.** Fleet telemetry is 30s old (server
sweep); the terminal is 1.5s (client poll). That split *is* the design — it is
what makes the overview free — but it should be understood before anyone reads
the gauges as real-time.

**Deliberately deferred.** Tier 2 = multi-open drawers (`Set` + compact mode,
~60 LOC), which is the literal original ask; Tier 3 = a tmux-style watch grid of
cropped live terminals, the only tier that costs real money and therefore should
be an explicit, bounded mode. Recommendation stands: ship Tier 1, use it, and
let real usage decide whether Tier 3 is a genuine need. The row now answers "is
this working, on what" for the whole fleet without opening anything.

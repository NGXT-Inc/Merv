# Sandbox Provider Configure — session log + production migration plan

Date: 2026-08-01 · Session scope: per-project compute-provider connections
(Sandboxes → Configure) · Status: **built + locally verified, adversarially
fact-checked (3-lens review), NOT Codex-reviewed, NOT deployed**

---

## 1. What this session built

The Sandboxes surface became two pages under one header — **Active** (the
existing fleet console, unchanged) and **Configure** (new) — with a guided,
per-provider connection wizard behind it:

- **Provider cards** for all 10 connectable clouds (Modal excluded — it is a
  managed-container runtime, not a paste-a-key cloud): Lambda Labs, Thunder
  Compute, Hyperstack, DigitalOcean, Verda (DataCrunch), Voltage Park,
  TensorDock, AWS EC2, Google Cloud, Microsoft Azure. Real vendor logo marks
  (`research_state_ui/public/providers/*.png`, served with the build; the
  icon component keeps a per-provider monogram fallback, so adding an
  eleventh provider needs a backend catalog entry + one PNG + one fallback
  label).
- **Setup wizard modal** (fixed 660px frame, constant height across steps):
  one credential per step, with per-field retrieval instructions and console
  deep links. Field specs + help text come from the backend catalog.
- **Save-then-verify**: the wizard saves the collected values, then makes one
  real read-only API call with them (STS `GetCallerIdentity` for AWS, OAuth2
  token mints for Verda/Azure, account/catalog reads for the rest). Success
  stamps `verified_at` ("connected · verified" chip); failure shows the
  provider's actual rejection with "Back — fix a value". Any credential
  VALUE write and any MODE change resets the stamp. Success details may
  include account identifiers (DigitalOcean account email, AWS account id) —
  human-only route, but they do appear in the response.
- **Enable switch gated on setup**: the switch only renders on set-up
  providers; `set_enabled(true)` on an unconfigured provider is a 400. The
  FIRST connection write (credentials or mode — including over a cap-only
  row) creates/flips the row **disabled**; enabling is the wizard's explicit
  final act. A daily-cap-only row on an env-configured provider stays
  enabled (setting a spending limit must not silently switch off the fleet
  default).
- **Platform credentials** ("RapidReview credentials"): providers in the
  platform set whose credentials exist in the deployment environment offer a
  choice screen — use the deployment's shared credentials or bring your own.
  Default platform set = `lambda_labs`; `MERV_PLATFORM_PROVIDERS` overrides.
- **Per-provider daily spend cap**: `daily_usd_limit` per (project, provider),
  editable on the card and in the wizard's finish step. Enforced at quota
  admission: NEW provisioning is refused once the project's UTC-day spend on
  that provider (summed from priced `sandbox_generations` rows, open
  generations billing to now, usage clamped to the UTC day) **has reached**
  the cap. It is a threshold on spend already incurred, not a pre-charge —
  a $5 cap admits requests until $5 has actually been spent today; a $0 cap
  blocks immediately. Unpriced hours count as $0; legacy generations with an
  empty provider tag never count. Existing budgets were tenant-total; this
  is the first per-provider dimension.
- **Secrets are write-only**: values for secret-marked fields (API keys,
  secret halves, SA JSON) never appear in any response — the overview
  reports set-ness only. Non-secret identifier fields (AWS access key ID,
  Azure tenant/client/subscription IDs, Verda client id, regions) DO echo
  back so forms re-render, and the overview GET is readable by any project
  principal including `mk_` machine keys. Raw JSON is read internally only.
- **Request-time disable gate**: `SandboxEngine.request` consults a
  composition-injected `provider_admission` hook right after project
  resolution; a row with `enabled = false` refuses the request on that
  provider outright — including requests that would merely have handed back
  an already-running sandbox (the hook fires before the reuse-live branch).
  Disabled means agents can't reach the provider through `sandbox.request`
  at all; already-provisioned VMs keep running (nothing tears them down).

### Also fixed in passing (travels with this change)

- **Fresh-Postgres bootstrap was broken on main**: `_sandboxes_uid_is_pk`
  (base store) probes with SQLite `PRAGMA`, which aborts an open Postgres
  transaction before its `information_schema` fallback runs — every
  from-scratch ladder replay died at migration 4 (same class as the known
  `_has_table` txn-abort gotcha; prod never replays migration 4, so it never
  saw this). Fixed with a Postgres override in `kernel/state/dialects.py`.
  The reference `deploy/docker-compose.yml` stack cannot boot without it.
- **Sandbox export law**: `from merv.brain.sandbox import *` is pinned to
  exactly `{SandboxBackend, SandboxEngine}`; `DisabledSandboxBackend` was in
  `__all__` on main (pre-existing violation). Removed from `__all__`; still
  importable by name.

### File manifest (this session's provider work)

New backend files:
- `merv/src/merv/brain/sandbox/adapters/provider_catalog.py` — 10
  `ProviderSpec`s: fields keyed by canonical `MERV_*` env names, `alt_env`
  vendor spellings (env detection only), per-field wizard `help`,
  `platform_default`, `env_configured()` / `env_values()`.
- `merv/src/merv/brain/sandbox/adapters/credential_check.py` — one authed
  probe per provider; every failure raises kernel `ValidationError` with an
  actionable reason (including "SDK not installed" for the aws/gcp extras).
- `merv/src/merv/brain/surface/sandbox_providers.py` —
  `SandboxProviderSettings` facade (APPLICATION_LAYER): overview /
  set_credentials / set_enabled / set_daily_limit / verify /
  ensure_provider_allowed. Store + `FleetResolver` + catalog + checks are
  composition-injected (no bootstrap imports).
- `merv/src/merv/brain/surface/transport/api/sandbox_providers.py` — routes.
- `merv/tests/surface/test_sandbox_providers.py` — 16 tests.

Modified backend files:
- `kernel/state/store.py` — SCHEMA table `sandbox_provider_settings`,
  migration `(43, "add_sandbox_provider_settings", "")` + `_has_table`-
  guarded handler, store methods (`upsert_sandbox_provider_settings`,
  `set_sandbox_provider_daily_limit`, `list_sandbox_provider_settings`,
  `sandbox_provider_credentials` — internal-only read).
- `kernel/state/dialects.py` — PG `_sandboxes_uid_is_pk` override (above).
- `sandbox/adapters/__init__.py` — `configured_backend_names()` extracted
  from `build_sandbox_backend`; re-exports catalog + checks for composition.
- `sandbox/core.py` — `provider_admission` hook call in `request()` (after
  project resolution, on the RESOLVED provider `caps.name`); builds the
  `AdmissionRequest` with `project_id` + `provider`, passed to BOTH the
  preflight and the transactional admission.
- `sandbox/models.py` — `ProviderAdmission` protocol.
- `sandbox/quotas.py` — `AdmissionRequest` gains `project_id`/`provider`;
  `provider_day_spend()` + `_check_provider_daily_limit` wired into
  `check_admission`. (Known minor: the limit lookup opens its own store
  connection rather than riding the admission transaction's — one extra
  short-lived PG connection per provisioning request.)
- `sandbox/__init__.py` (export law), `sandbox/sandbox.md` (file map),
  `surface/surface.py` (composition), `surface/transport/api/app.py`
  (router registered inside the sandbox-enabled gate).
- Tests: `tests/compat/test_release_db_compat.py` (ladder-head pin 42 → 43),
  `tests/structure/test_module_boundaries.py` (FILE_LAYERS entry for the
  facade; `sandbox_provider_settings` → KERNEL in TABLE_OWNERS).

UI (research_state_ui):
- New: `components/ProviderConfig.jsx`, `components/ProviderSetupModal.jsx`,
  `components/ProviderIcon.jsx`, `public/providers/*.png`.
- Modified: `pages/Sandboxes.jsx` (Active | Configure switch,
  `?view=configure` deep link), `api.js` (5 client methods),
  `styles/global.css` (`sbx-view`, `sbxp-*`, `sbxpw-*`; cards pin their
  footer row so buttons align per grid row).

Local verification: 16/16 feature tests; full `merv` suite on this tree =
**1655 passed / 1 failed / 44 skipped** — the one failure is the
pre-existing `research_core.md` 100-line-cap structure test (fix in flight
in a separate session). PG-live tests skip unless a Postgres is reachable;
re-run them against the compose stack once Docker disk is cleared.

---

## 2. Data model and API contract

### Table (kernel-owned; SCHEMA + migration 43)

```sql
CREATE TABLE IF NOT EXISTS sandbox_provider_settings (
  project_id      TEXT NOT NULL,
  provider        TEXT NOT NULL,           -- canonical driver name
  credentials     TEXT NOT NULL DEFAULT '{}',  -- JSON keyed by MERV_* field names
  enabled         INTEGER NOT NULL DEFAULT 1,
  credential_mode TEXT NOT NULL DEFAULT '',    -- '' | 'own' | 'platform'
  daily_usd_limit REAL,                        -- NULL = uncapped
  verified_at     TEXT NOT NULL DEFAULT '',
  updated_at      TEXT NOT NULL,
  PRIMARY KEY (project_id, provider)
);
```

Purely additive; no existing table/column changes, no backfill. **On
Postgres the table is actually created by the pre-ladder schema pass**
(`translate_schema_to_postgres` rewrites `INTEGER`→`BIGINT`,
`REAL`→`DOUBLE PRECISION`), so migration 43 itself is a guarded no-op that
just records the ledger row. Operational consequence: if the DDL ever
failed on prod it would surface as a crash-loop at boot (pre-ladder), not
as a failed migration step.

### Routes (registered only when sandboxes are enabled)

| Route | Method | Auth |
|---|---|---|
| `/api/projects/{id}/sandbox-providers` | GET | any project principal |
| `/api/projects/{id}/sandbox-providers/{p}` | PUT `{values?, mode?}` | browser session or local principal |
| `/api/projects/{id}/sandbox-providers/{p}/enabled` | POST `{enabled}` | browser session or local principal |
| `/api/projects/{id}/sandbox-providers/{p}/daily-limit` | POST `{daily_usd_limit}` | browser session or local principal |
| `/api/projects/{id}/sandbox-providers/{p}/verify` | POST | browser session or local principal |

`mk_` / `rr_sk_` machine keys can read the overview (set-ness + non-secret
identifiers, never secret values) but get 403 on every write. Empty-string
field value clears that field; omitted fields keep their stored value;
blank secret input in the wizard means "keep".

### Semantics that protect existing behavior

- **No row = today's behavior.** Env-fleet providers without a settings row
  stay enabled by default; prod agents keep procuring exactly as before the
  deploy, with zero rows in the new table. Both the disable gate and the
  cap check no-op on an empty settings set.
- **First connection write = disabled** (credentials or mode, including
  over a cap-only row). Cap-only rows on env-configured providers stay
  enabled by design.
- `setup_complete` is a precedence chain, not a plain disjunction: an
  explicit `platform` mode is judged ONLY by platform availability — if the
  operator later removes the platform key or narrows
  `MERV_PLATFORM_PROVIDERS`, that row reads not-set-up even when own
  credentials are also saved, until the user re-runs the wizard in "own"
  mode. Otherwise: own creds complete, else (no explicit mode ∧
  env-configured).

---

## 3. Production migration plan

Prod today: Azure VM (`ssh azureuser@20.98.245.95`, checkout
`~/Research-Suite`, ops dir `~/research-suite-vm`), UI deployed by Vercel
from `main`. **Prod's Postgres is at migration 40** (last deploy 7/27).
The Configure tab currently 404s against prod because none of these routes
exist there.

### What actually ships (read this first)

`main` is 4 commits ahead of `origin/main` (agent-sessions + cloud adapters
+ consolidation + minimal-plans), and the working tree adds this feature
plus the fleet-liveness / settings work from parallel sessions. Owner's
call: **one big commit for everything in the tree.** A fast-forward deploy
therefore runs **three migrations, not one**:

- **41 `add_agent_sessions`** — creates `agent_sessions` + its indexes
  (three partial UNIQUE among them).
- **42 `add_consolidation`** — rebuilds `agent_sessions`
  (create/copy/drop/rename; on prod it rebuilds the table 41 just created,
  i.e. empty — benign, but it is NOT "additive") and creates four more
  tables (`experiment_workspaces`, `consolidation_proposals`,
  `consolidation_decisions`, `reflection_advances`).
- **43 `add_sandbox_provider_settings`** — this feature (ledger no-op on
  PG, see §2).

Consequence for review: the pre-deploy Codex review must either cover the
riding features (agent-sessions/consolidation, fleet-liveness, settings UI)
or the owner explicitly accepts shipping them together. `MAX(version) = 43`
after deploy does NOT distinguish "43 only" from "41+42+43" — all three are
expected here.

### Step 0 — gates

1. **Codex review** (standing law), suggested scopes: (a) store/migrations
   41–43 + quota admission (money-touching), (b) credential handling
   (write-only contract, verify flow), (c) UI, (d) the riding features if
   not separately reviewed.
2. One big commit per owner instruction (exclude `cache/` — runtime
   artifact, now gitignored).
3. Suite state as recorded in §1 (1655/1/44; the 1 is pre-existing).

### Step 1 — backend deploy (VM)

```
ssh azureuser@20.98.245.95
# 1. pg_dump backup per runbook  ← non-negotiable this time: migration 42
#    rebuilds a table, so take the dump immediately before the pull.
# 2. cd ~/Research-Suite && git pull --ff-only
# 3. cd ~/research-suite-vm && ./control-up.sh
```

- **Env, required: none.** The feature works with zero new env vars.
- **Env, optional:** `MERV_PLATFORM_PROVIDERS` (default `lambda_labs`). The
  VM already carries `MERV_LAMBDA_API_KEY`, so Lambda's "RapidReview
  credentials" option lights up automatically.
- **Deps:** AWS verify needs `boto3` (already in the control extra). GCP
  verify needs `google-auth` (`merv[gcp]`) — NOT in the control extra;
  until added, GCP verify returns the explicit "google-auth is not
  installed" failure. Decide before or after deploy; nothing breaks either
  way.
- Watch the boot logs: a schema failure would crash-loop BEFORE the
  migration ladder (see §2). If the container loops, restore is: checkout
  previous release + restart (the dump exists for the 42 worst case).

### Step 2 — UI deploy

Merge → Vercel builds from `main` (~15 min). Logo PNGs are static assets in
the bundle; no external requests at runtime; no Supabase/CORS changes; the
new routes ride the existing auth middleware.

### Step 3 — post-deploy verification (~10 minutes, use a SCRATCH project)

1. Routes live: `GET /api/projects/<id>/sandbox-providers` (browser
   session) returns the 10-provider overview — this is the "new code is
   live" signal (`/api/meta` version was not bumped, so it proves nothing).
2. `SELECT MAX(version) FROM schema_migrations` → 43, and spot-check the
   new tables exist (`sandbox_provider_settings`, `agent_sessions`,
   `consolidation_proposals`).
3. UI: Sandboxes → Configure renders 10 cards; Lambda shows
   "via environment" + DEFAULT + switch.
4. Wizard smoke on Lambda — **on the scratch project only**: choosing
   "Use RapidReview credentials" saves a row that is DISABLED until the
   final "Enable for agents" click, i.e. this step switches Lambda OFF for
   that project mid-flow (and a failed verify leaves it off). Finish the
   wizard and click Enable, or flip the switch after. Never run this smoke
   on a live research project.
5. Machine-key negative check: `PUT .../sandbox-providers/aws` with an
   `mk_` key → 403.
6. Agent regression: `sandbox.request` on an EXISTING project (zero
   settings rows) still provisions on the env fleet unchanged.
7. Daily-cap smoke — remember it is a threshold on spend already incurred:
   set a **$0** cap (blocks immediately: `0 >= 0`) on the scratch project's
   default provider, confirm `sandbox.request` refuses with the
   daily-limit error, then clear the cap. A $0.01 cap on a fresh project
   would NOT refuse — it would provision a real, billable VM.

### Rollback

- Schema: additive for 43; migration 42's rebuild ran against an empty
  table on prod, so data risk is nil, but 41/42 are not code-reversible —
  the new tables stay (inert under old code). `sandbox_provider_settings`
  rows likewise stay inert.
- **Behavioral caveat:** rolling the backend back silently disarms both new
  money controls — daily caps and disable switches stop being enforced
  while their rows persist, and a not-yet-rolled-back UI keeps displaying
  them as active. If you roll back the backend, roll back the UI too (or
  warn users).

---

## 4. Explicitly OUT of this change (phase 2)

1. **Saved credentials do not yet drive provisioning.** The fleet is still
   built from `MERV_EXECUTION_BACKENDS`; a connected-but-not-in-fleet
   provider says so on its card. Phase 2 = per-project credential-backed
   driver construction — including reaper/cleanup coverage for VMs procured
   with DB credentials, which is billing-safety-critical and needs its own
   scoped review.
2. **Known cosmetic/edge issues:**
   - Stacks that set `AWS_ACCESS_KEY_ID` for S3-compatible object storage
     (MinIO in the reference compose) make the AWS card read "via
     environment" — env detection can't distinguish storage creds from EC2
     creds yet. Check whether the prod VM sets AWS_* for storage.
   - GCP env-mode verify: `GOOGLE_APPLICATION_CREDENTIALS` is a file path
     but the check expects inline JSON — misleading failure for
     env-configured GCP. Wizard-saved GCP credentials verify fine.
   - Live provider smoke tests still pending for voltage_park / aws / gcp /
     azure adapters (pre-existing `NEEDS LIVE SMOKE TEST` flags).
3. **Docker reference stack on this laptop** is parked on a full Docker VM
   disk (~37 GB reclaimable, mostly other projects — needs owner-approved
   prune). The stack publishes 8787 by default; this session used a
   local override (ports + open dev plane + fake platform key) kept outside
   the repo. Fresh-from-scratch boot works now thanks to the dialects fix.

## 5. Dev pointers

- Local wizard demo: brain on :8797 (fake `MERV_LAMBDA_API_KEY` so the
  platform path renders; verification against it fails by design), UI on
  :5173 (`ui-dev` launch config), project "Wizard Dev";
  `?view=configure` deep-links the page.

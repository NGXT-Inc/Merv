# Control plane / data plane split for cloud, multi-user backend

**Status:** IMPLEMENTED (cloud backend migration Phases 0–9; Phase 4b removed
the split-mode local daemon) · **Drafted:** 2026-06-07 · **Landed:** 2026-06-13

> This doc was the original proposal. The split is now built end-to-end. Use
> **`docs/CONTROL_PLANE_OPERATIONS.md`** for operating the control plane (modes,
> env, cleanup jobs, version floor, deploy). The module-by-module assignment
> below is the public architecture record.

## Why this doc exists

Today the backend can run entirely on the user's machine. In local mode the
HTTP backend (`python -m backend.transport.http_server`) and the stdlib-only
stdio proxy (`python -m mcp_server`) are local. In split mode the long-running
local daemon is gone: the per-session stdio proxy is the local data plane. It
reads repo-relative files, computes hashes, captures gated bytes, resolves the
repo→project link file, and submits explicit metadata/bytes to hosted control.

When the backend moves to the cloud and serves multiple users, that assumption
breaks. This doc proposes splitting the monolith into a **cloud control plane**
and a **local data plane**, and pins down exactly which existing module lands on
which side.

## The load-bearing constraint

> **The cloud backend cannot see a user's local filesystem.**

A cloud-hosted backend has no access to `experiments/<id>/`, the user's
repo files, or their SSH `known_hosts`. Therefore **any code that reads local
files, writes local files, or owns local SSH key material must run in a process
on the user's machine.** Everything else — orchestration, records, provider
credentials, authz — can and should move to the cloud.

This single rule determines the entire split.

## Target topology

Two runtime roles:

```
┌──────────────────────────────────────────────────────────────────────┐
│  USER MACHINE                                                          │
│                                                                        │
│   Agent (Codex / Claude Code)                                          │
│        │ stdio                                                         │
│        ▼                                                               │
│   MCP server  ──────────── control-plane tools ──────────► CLOUD       │
│   (stdio proxy) ───────── data-plane submissions ────────► CLOUD       │
│        │                                                               │
│        ├─ local file reads / hashing / validation                      │
│        └─ repo→project link file (`project_links.sqlite`)              │
└──────────────────────────────────────────────────────────────────────┘

CLOUD (multi-tenant)
   Control plane: auth, ownership, project/experiment/claim/review records,
   sandbox lifecycle, provider credentials + billing, SSH public-key authorization,
   status aggregation, cleanup jobs.
```

- **Cloud control plane** — multi-tenant, the source of truth for orchestration
  and records. Provisions VMs, but never touches a user's filesystem.
- **MCP server** — the stateless local data plane in split mode. Control-plane
  tool calls go to the cloud. Data-plane tools run local reads/validation in
  the proxy and submit explicit facts/bytes to neutral control endpoints.

### Phase 4b update: the daemon is gone in split mode

The old design kept a long-running local data-plane daemon. Phase 4b moved its
two split-mode duties into the proxy: artifact byte shipping and repo↔project
link resolution. The on-disk `project_links.sqlite` format is unchanged for
backward compatibility. Local mode still uses the in-process HTTP backend; it
never depended on the split daemon.

## The split, module by module

The current composition root is [`backend/app.py`](../backend/app.py), which
wires the services below. Here is where each lands.

### Stays in the cloud (control plane)

| Component | Module today | Why it's cloud |
|---|---|---|
| Projects | `services/projects.py` | Pure records + ownership; no local FS. |
| Claims | `services/claims.py` | Records. |
| Experiments + state machine | `services/experiments.py` | Records + transition rules. |
| Reviews | `services/reviews.py` | Records + reviewer capabilities. |
| Workflow orchestration | `services/workflow.py` | `status_and_next` is pure logic over records. |
| Permissions / authz | `services/permissions.py` | Becomes the real multi-tenant authz layer. |
| Sandbox hardware catalog | `execution/backends/*` via `SandboxBackend.hardware_catalog` | Provider-specific GPU/pricing metadata stays behind the execution backend protocol. |
| Sandbox **lifecycle records + provisioning** | `services/sandboxes.py` (provision/terminate/reconcile, lifecycle rows) | Calls Thunder/Modal/Lambda; holds provider creds; no FS needed. |
| Execution backends | `execution/backends/{thunder_compute,modal,lambda_labs}` | Provider credentials + VM API calls belong server-side. |
| Provider credentials + billing | (Thunder/Modal/Lambda config) | Must never sit on user machines in a multi-tenant world. |
| Durable state | `state/store.py` | Becomes the multi-tenant DB (Postgres), keyed on user/project, not `repo_root`. |
| Audit / activity | `state/activity.py`, `state/tool_calls.py` | Cloud-side audit per tenant (a thin local mirror is optional). |

### Must run locally (data plane)

| Component | Module today | Why it's local |
|---|---|---|
| **Local retained-output target layout** | [`execution/sync_dirs.py`](../backend/execution/sync_dirs.py) path helpers | `experiments/<id>/` is a local path. |
| **Experiment folder materialization** | `dataplane/experiment_folders.py` (`experiment.materialize_folders`) | Creates repo-local `experiments/<name>/` directories; hosted control records the experiment but cannot mkdir in the checkout. |
| **Caller SSH key material** | caller-managed keys under `.research_plugin/sandboxes/keys` or equivalent | Private key stays on the user's machine; split `sandbox.request` sends only `public_key`. |
| **Local-mode sandbox dispatcher + conn files** | `sandbox/sandbox_support.py`, local `dataplane` worker | Local-mode helper the agent shells out to. Split mode does not mint or persist daemon conn files. |
| **Sandbox retained output pull** | `dataplane/sandbox_outputs.py` (`sandbox.pull_outputs`) | Uses local SSH key material and writes selected remote outputs into the repo-local experiment folder. |
| **Resource file observation + validation** | `dataplane/resource_observer.py`, `dataplane/resource_validation.py` (`resource.register_file`, `resource.validate`) | Hashes/reads **repo-relative local files**; preflight lint reads artifact bytes before any cloud-state mutation. |
| **Local HTTP discovery marker** | `daemon_marker.py`, `.research_plugin/daemon.json` | Local-mode HTTP process discovery (name retained for compatibility). |

### Splits across the seam

A few responsibilities are genuinely two-sided. The **bytes/IO half is local;
the record/metadata half is cloud.**

- **Sandbox output handoff.** Cloud sets up the remote `/workspace/<name>`
  contract and returns SSH details. In local mode `sandbox.pull_outputs`
  explicitly pulls light files back with rsync. In split mode the proxy returns
  rsync guidance for caller-owned keys; heavy artifacts should go to durable
  storage.
- **Resources.** The local proxy reads the file and computes the version hash;
  cloud stores the resource record and immutable version history.
- **SSH access.** Cloud authorizes a caller-supplied public key and owns
  credential validity/rotation. The caller keeps the private key; `sandbox.get`
  reports `public_key_source`.
- **Tenancy routing.** Today [`project_router.py`](../backend/daemon/project_router.py)
  multiplexes local-mode repos into per-`repo_root` app instances — a local,
  directory-keyed primitive. In production, **tenancy (user/project) moves to
  the cloud**, while the proxy reads the local directory mapping
  (`repo_root` ↔ `project_id`) from `project_links.sqlite`. Anything keyed on
  `repo_root` is, by definition, local.

## The seam: contracts between cloud and local

### Sandbox SSH handoff (cloud to local)

When the agent procures a sandbox in split mode, the caller supplies an
OpenSSH public key. The cloud authorizes it and returns SSH details and remote
workspace paths. The response includes `public_key_source: "caller"`; no
daemon-minted private key path is returned.

```jsonc
{
  "experiment_id": "...",
  "sandbox_uid": "...",
  "sandbox_id": "...",
  "ssh": { "host": "...", "port": 22, "user": "root" },
  "remote": { "experiment_dir": "/workspace/<name>",
              "data_dir": "/workspace/data" },
  "local": { "retained_output_dir": "experiments/<name>/" }
}
```

There is no background sync or daemon-owned transfer lease. Output handoff is
explicit: the agent calls `sandbox.pull_outputs` to copy selected light files
back over SSH before release, and uses durable storage tools for large
artifacts.

### Local command material

Local mode still renders `.research_plugin/sbx` command material from the
in-process worker. Split mode does not mint or own private keys; the agent uses
its caller-owned key and the returned SSH facts directly.

### MCP tool surface

Sandbox tools should stay lifecycle-oriented. Split mode keeps caller key
custody outside hosted control; retained files are copied explicitly by the
agent over SSH or uploaded to durable storage.

- `sandbox.request` — caller public-key authorization plus control-plane provisioning.
- `sandbox.get` — aggregate control row facts plus local-mode enrichment when available.
- `sandbox.pull_outputs` — local-mode SSH/rsync transfer into the local experiment folder; in split mode returns rsync guidance and points heavy artifacts at storage tools.
- `sandbox.release` — control-plane lifecycle termination after retention confirm.
- `sandbox.extend` — control-plane lifetime extension of the reaper deadline
  when the provider supports it and persisted activity is present, subject to
  tenant quota/spend policy.

## Cross-cutting concerns to design before this is real

1. **Production auth.** The current private operator-run deployment has no user
   auth. Before broad exposure, the local daemon must authenticate to the cloud
   *as the user* so ownership checks mean anything. Device-flow OAuth with a
   local refresh token is the expected shape.

2. **SSH credential model.** Prefer an **SSH CA**: the local daemon generates a
   keypair (private key never leaves the machine), the cloud signs the public
   key into a short-TTL certificate scoped to one sandbox. This keeps the
   private key local *and* puts validity/revocation/rotation in the control
   plane. (Today `_ensure_keypair` generates a long-lived local key and hands
   the public key to the backend; the CA model is the production evolution.)

3. **Explicit retention is a user-facing contract.** Release and reaping destroy
   sandbox-local files. The UI and tool hints must keep the retention checklist
   visible so agents copy or upload important outputs before termination.

4. **Provider-credential ownership — a fork, not a footnote.**
   - *Platform-owned* Modal/Lambda accounts with per-user billing attribution:
     best UX, but you become a compute reseller (abuse/quota/billing risk).
   - *Bring-your-own*, user-scoped, encrypted at rest: no fronted spend, worse
     UX. Pick deliberately.

5. **Cleanup jobs for abandoned VMs.** With provisioning server-side, the cloud
   must reap VMs whose owner/session has gone away (today reconciliation is
   best-effort and local).

6. **Per-sandbox isolation.** One namespace per user/experiment; per-sandbox SSH
   credentials, never a shared global key.

## Why direct local↔VM, not a cloud relay

An alternative is routing bytes user → cloud blob store → VM, so the cloud can
"see" the data. We reject it as the default: it doubles byte movement, adds
storage cost, and *still* needs a local agent to push from the filesystem — so
it doesn't remove the local component. Direct local↔VM SSH is simpler and
cheaper for large artifacts. The one reason to revisit is unreachable VMs
(NAT/firewall); Modal/Lambda VMs are generally directly reachable, so direct
stays the default with relay as a fallback transport.

## Suggested migration path (incremental)

This is evolution, not a rewrite — the local daemon already owns local data-plane
state.

1. **Carve the seam in-process first.** Split `SandboxService` into a
   control half (lifecycle records, provisioning) and a data half (keys,
   local dirs, resource observation) behind an interface, while both still run locally. No
   behavior change; just a clean boundary.
2. **Define the sandbox identity and attachment contract** so project sandboxes
   can be standalone, attached to experiments, or reattached without VM churn.
3. **Stand up the private cloud control plane** (durable DB, provisioning,
   credential issuance; real user auth comes later). Point the MCP server's
   control-plane tools at it.
4. **Ship the local data-plane daemon** as the slimmed-down successor to
   `backend.transport.http_server`: data half only, registering with the cloud
   control plane.
5. **Keep file handoff explicit**: no automatic daemon copy job or transfer lease.

## Open decisions

- SSH CA vs. ephemeral-keypair-per-session (recommend CA).
- Platform-owned vs. bring-your-own provider credentials.
- Where the activity/audit log lives — cloud-only, or cloud with a local mirror
  for offline debugging.
- One local daemon per machine vs. per-user on shared machines.

## Related

- [`STARTUP_CHEATSHEET.md`](STARTUP_CHEATSHEET.md) — current process topology
  (daemon vs. MCP proxy).
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — current component architecture.
</content>

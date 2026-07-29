# Sandbox module

## Purpose and boundary

`merv.brain.sandbox` is the provider-neutral control plane for temporary
research compute. `SandboxEngine` is its only public control object: surface
tools and HTTP views call the engine, while provider SDKs, SQL/state primitives,
and transport contracts remain outside the engine API. The package owns
reservation, provisioning, attachment, observation, retained-output commands,
extension, release, expiry/idle cleanup, and spend/accounting projections. It
does not own experiment workflow, authentication, HTTP/MCP rendering, provider
credentials, or artifact/blob persistence.

## Main control and data flow

1. Composition builds one or more drivers through `execution.driver_registry`
   and `execution.build_sandbox_backend`; a `MultiplexingSandboxBackend` routes
   provider-qualified IDs while lazily importing only configured adapters.
2. `SandboxEngine.request` validates caller-owned SSH material and resource
   selection, consults `QuotaService`, and reserves a durable `provisioning`
   row through `SandboxStorage`. The durable row, not an in-process thread, is
   authoritative.
3. `SandboxAcquisition` provisions asynchronously. Phase callbacks update the
   row; the provider resource ID is persisted immediately after creation so a
   crash cannot erase the cleanup handle. Publishing `running` and opening its
   spend generation happen in one store transaction.
4. The engine renders stable agent views and delegates provider work through
   `SandboxBackend`: liveness, transcript/run reads, metrics, SSH endpoint
   refresh, secret delivery, extension, and termination. Management keys and
   write-only transient secrets are addressed by immutable `sandbox_uid`.
5. `SandboxRunLedger`, `RunsObserver`, `SandboxMetrics`, and `TranscriptCache`
   reconcile remote receipts and observations into bounded, owner-checked
   views. Remote paths come from `sandbox_paths`; run/transcript wire formats
   live under `execution`.
6. Release, failed provisioning, expiry, idleness, and stale provisioning all
   converge on `SandboxLifecycle`. `SandboxScheduler` supplies cadence only:
   lifecycle and acquisition own decisions and destructive effects.

## Responsibilities by area

- `core.py`: public operations, validation, response projection, and component
  wiring; it must not contain provider SDK, subprocess, HTTP, or SQL details.
- `storage.py`: durable rows, attachments, generations/spend, events, and
  compare-and-set transitions with project/user ownership guards.
- `acquisition.py`, `sandbox_lifecycle.py`, `scheduler.py`: asynchronous acquire,
  tri-state liveness and fenced cleanup, then periodic ordering respectively.
- `observation.py`, `sandbox_heartbeat.py`, `quotas.py`: run/metrics observation,
  activity/idle policy, and tenant capacity/spend limits.
- `sandbox_backend.py`: provider protocol, capability model, request/result
  values, normalized errors, and conservative default behavior.
- `execution/`: portable bootstrap/SSH/run/transcript formats plus driver
  discovery, multiplexing, and concrete provider adapters. Each adapter keeps
  API/config/catalog translation behind the common backend contract.
- `keys.py`, `mgmt_keys.py`, `managed_mgmt_keys.py`, `ssh_keys.py`: ephemeral
  secret custody and management-key implementations; caller private keys never
  enter durable brain state.

## Safety and consistency invariants

- A provider error is `unknown`, never proof that a resource is gone. Only
  confirmed absence permits a terminal row; otherwise the row stays
  `cleanup_pending` and is retried because it may still bill.
- Destructive transitions are fenced by `sandbox_uid`, project ownership, row
  status/phase, and cleanup claim tokens. A late worker may not overwrite a
  newer request or reclaimed cleanup attempt.
- Provider ownership is durable. New IDs are `<provider>:<native-id>`; legacy
  IDs must be qualified from the row's recorded provider. Unknown/unconfigured
  owners fail closed rather than falling through to the current default.
- An experiment has at most one default active sandbox, while explicit
  attachment supports controlled sharing. Ownership fields are immutable and
  every read/write is project-scoped.
- Expiry enforcement protects provider billing. Scheduler order is: expiry,
  run reconciliation, idle judgment, stale-provision cleanup, then retry of
  uncertain cleanup; detached run receipts must be refreshed before idleness.
- Terminal state closes the spend generation and removes brain management keys
  and transient secrets. Valuable remote outputs must be retained before the
  confirmed release step.
- Provider adapters must satisfy `SandboxBackend`, translate errors into the
  shared taxonomy, persist an ID through `on_created` as soon as one exists,
  and avoid importing optional SDKs until their driver is selected.

## Integration boundaries

Inbound callers are the control composition root, tool handlers, and HTTP views;
they depend only on `SandboxEngine` and its JSON-compatible results. Durable
state is accessed through kernel store/management-key ports. Outbound compute
access is exclusively through `SandboxBackend`; VM-like drivers share
`VmSshSandboxBackend`, bootstrap scripts, and management SSH helpers, while
Modal implements the same contract with its native runtime. Tests under
`tests/sandbox` enforce backend contracts, lifecycle/event semantics, tenancy,
and this guide's maintenance rules.

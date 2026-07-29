# Artifacts

## Purpose

`artifacts` owns the durable evidence lifecycle. It associates typed files with
research targets, stores content-addressed bytes, manages document figures, and
freezes the evidence composition used by workflow transitions. It does not own
research-target state, workflow gates, transport/authentication, database schema,
or the blob-store implementation.

## Files and responsibilities

- `artifacts.py`: stateful application service for submission, upload, reads,
  live-slot replacement, system pinning, figure uploads, and history sealing.
- `models.py`: immutable value types, read modes, completion results, and the
  `ArtifactTargets` protocol through which Research supplies target facts.
- `__init__.py`: the deliberately small public import surface.

## Agent upload flow

1. `submit` validates the target/role association and reflection-lens rules,
   normalizes the caller's display path, sweeps expired records, and resolves
   the target through `ArtifactTargets`.
2. In one state transaction it creates a `pending` artifact with the resolved
   project and attempt plus an opaque, single-use token expiring after 15
   minutes. The returned path is metadata; the brain never reads the caller's
   filesystem.
3. `upload_cap` authenticates the token before an HTTP body is accepted and
   returns the role-specific artifact cap or the shared figure cap.
4. `complete_upload` re-resolves the target. A deleted target or superseded
   attempt invalidates and removes the pending token before returning an error.
5. For a valid artifact, bytes are placed in `EvidenceBlobStore` under the
   project namespace before the row records their digest. The service replaces
   the matching live slot, marks the new row complete, clears the token, emits
   `artifact.submitted`, and returns digest/size metadata.
6. Markdown roles that support figures are parsed for local image links.
   Validated, deduplicated links receive independent pending figure tokens;
   each figure completion enforces its cap and stores content by digest.

## Live composition and immutable history

- A slot is `(project, target type/id, role, attempt, lens, path)`. Completing a
  new agent artifact deletes older unsealed rows in that slot, except artifacts
  protected by a published reflection. Sealed rows are never replacement
  candidates.
- `pin` is the trusted system-write path: it writes bytes without a token and
  replaces the live system artifact for the same target, role, and attempt
  while retaining sealed rounds.
- `seal` must run on Research's existing transaction. It creates a `Submission`
  and stamps every complete, unsealed artifact for the resolved target attempt
  with that submission ID, making the workflow transition and evidence snapshot
  atomic.
- `history` groups complete artifacts and submissions by target. Optional TLDRs
  are best-effort: missing blob content must not erase durable history.

## Read behavior

- `get` deduplicates IDs while preserving request order. `metadata` avoids blob
  reads, `content` adds bytes when available, and `document` additionally lists
  completed figure links and propagates blob-read failures.
- `scan` returns complete metadata with optional project, target, and role
  filters in deterministic order. `figure` returns bytes only for a completed
  link and returns `None` when the row or blob is unavailable.

## Invariants

- Every write is project-scoped and target-resolved; caller-supplied project or
  attempt facts are never trusted without `BaseStateStore`/`ArtifactTargets`.
- Upload credentials are random, expiring, single-use, and stored only on
  pending rows. Completion clears them; sweeps delete expired pending artifacts
  and figures.
- Artifact roles and target types come from `merv.shared.artifact_roles`;
  legacy roles remain readable but cannot be newly submitted.
- Blob keys are project namespaces plus SHA-256 digests. State rows point only
  to successfully stored bytes; orphaned blobs are acceptable after a later
  transactional validation failure.
- Database mutations and event emission share state transactions. Blob storage
  is outside those transactions, so callers must not infer row existence from
  blob existence.

## Integration boundaries

`BaseStateStore` supplies transactions, project enforcement, ordering, IDs, and
events. `EvidenceBlobStore` supplies content-addressed byte persistence.
`ArtifactTargets` is the inversion boundary to Research for target resolution
and publication protection. Shared role and Markdown helpers own caps and link
rules. Surface adapters translate MCP/HTTP calls and upload tokens; Research
calls `seal` and consumes `history`. Changes to row shape require coordinated
Kernel schema/migration work, and changes to roles or figures require their
shared-policy owners.

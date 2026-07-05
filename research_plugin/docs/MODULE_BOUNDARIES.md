# Module Boundaries

Target shape: a modular monolith — one kernel, five modules plus the MLflow
extension, and a surface that composes them.

```
                    ┌───────────────────────── SURFACE ─────────────────────────┐
                    │  tools/ transport/ composition/ control/ daemon/          │
                    │  dataplane/ app config client_cli  (imports anything)     │
                    └───────┬──────────┬──────────┬─────────┬─────────┬─────────┘
                            ▼          ▼          ▼         ▼         ▼
   MLFLOW ──────▶ RESEARCH_CORE   ARTIFACTS   OBJECT_   SANDBOX     FEED
 (extension)          │    │          │       STORAGE                │
                      │    └─────────▶│          ▲                   │
                      │   (allowance) └─────────▶│◀──────────────────┘
                      ▼                    (allowances)
                    KERNEL   (db/transactions/events/ids — imports only itself)
```

## Import law

- kernel imports only kernel.
- Each module imports only itself + kernel, plus these ratified allowances:
  - `research_core -> artifacts`: workflow gates judge pinned artifact bytes.
  - `artifacts -> object_storage` and `feed -> object_storage`: resource
    versions and feed images persist their bytes through the blob stores.
  - `mlflow -> research_core`: the extension reads experiment records.
- surface imports anything. **Nothing imports surface.**

## Module → package mapping

| Module         | Backend code                                                                |
|----------------|-----------------------------------------------------------------------------|
| kernel         | `state/*` (minus blobs; incl. `tool_call_stats`), `ports/*` (incl. the `AdmissionRequest` contract in `ports/quota_admission`), `utils`, `env`, `version`, `secret_tokens` |
| research_core  | workflow/experiments/claims/reviews/syntheses/projects services + views, `graph_refs`, `reflection_tools`, `domain/*` (minus overrides) |
| artifacts      | `artifacts/*` (resources, pinned + PinnedStore facade, roles, markdown_images, figure_view, resource_selection) |
| object_storage | `storage/*`, `state/{blobs,s3_blobs}`, `domain/storage_guidance`             |
| sandbox        | `services/sandbox/*`, `sandbox/*` (incl. the `mgmt_keys`/`managed_mgmt_keys` custody adapters), `execution/*`, `services/{transcript_cache,quotas}`, `domain/sandbox_paths`, `ssh_keys` |
| feed           | `services/{feed,feed_unfurl}`, `domain/{feed_images,feed_policy}`            |
| mlflow         | `mlflow/*` (extension, incl. its own env config in `mlflow/config`)          |
| surface        | `tools/*`, `transport/*`, `composition/*`, `control/*`, `daemon/*`, `dataplane/*`, `app`, `config`, `client_cli`, glue services (`permissions`, `identity`, `cleanup`), `local_runtime`, `workspace`, `observability` |

The authoritative, file-exact table is `FILE_MODULES`/`PACKAGE_MODULES` in
`tests/structure/test_module_boundaries.py`.

## How the ratchet works

`tests/structure/test_module_boundaries.py` AST-scans every import (top-level
and function-local) in backend production code, maps importer and imported
file to modules, and checks the edge against the law above. Violating pairs
were frozen in `GRANDFATHERED`, which only shrinks — and phase 4a drove it to
**zero**: every import now follows the law, and any new cross-module import
fails immediately. New backend files must be classified in the same test
before they can land.

Two module-content rules ride along with the import law:

- **Sandbox de-domaining:** sandbox-module files must not embed SQL naming
  research-core tables (`experiments`/`claims`/`reviews`/`syntheses`) —
  enforced by `test_sandbox_module_sql_names_no_research_core_tables`.
  Attachment ids are opaque labels inside the sandbox module; the surface
  injects the experiment existence/scope check
  (`build_experiment_attachment_check`) and the storage guidance prose
  (`storage_hint` / `storage_guidance` constructor params).
- **Provider neutrality:** behavior keyed on a provider name is forbidden
  outside `execution/backends/<provider>/`; differences are expressed as
  `BackendCapabilities` flags (enforced by
  `test_services_do_not_dispatch_on_provider_name_literals`).

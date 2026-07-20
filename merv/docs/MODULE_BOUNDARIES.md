# Module Boundaries

The brain is a modular monolith. Two independent classifications describe it:

- a **component** says which capability owns a file;
- a **layer** says what architectural job that file performs.

This distinction is intentional. Research, Artifacts, Sandbox, and Feed are
business components. MLflow is an outbound tracking integration, and concrete
object storage is outbound infrastructure. A provider driver can therefore be
adapter-layer code owned by Sandbox; a folder name does not make an adapter a
business authority.

```text
                       bootstrap / composition
                    /          |             \
                   v           v              v
              delivery --> application <-- adapters
                                  |
                                  v
                         component facades/ports
                                  |
                                  v
                                kernel
```

The local proxy and pure shared packages sit outside this brain-only law.

## Component law

Every `src/merv/brain/**/*.py` file has exactly one component. Deepest-prefix
classification plus file overrides handles mixed packages.

| Component | Physical code today | Meaning |
|---|---|---|
| Kernel | `kernel/**` | shared contracts, state floor, IDs, events, utilities |
| Research | `research_core/**` | experiment/review/reflection/project authority |
| Artifacts | `artifacts/**` | resources, associations, pinned evidence |
| Sandbox | `sandbox/**` | lifecycle and provider-driver capability |
| Feed | `feed/**` | feed records and advisory policy |
| Application | `application/**` | cross-component commands and reactions |
| Tracking integration | `mlflow/**` | MLflow implementation of tracking ports |
| Storage | `object_storage/**` | byte/object adapters plus the legacy ledger service |
| Surface | `surface/**` | HTTP/MCP delivery and the co-located composition root |

The exact component import matrix is:

| Importer | May import |
|---|---|
| Kernel | Kernel |
| Research | Research, Artifacts, Kernel |
| Artifacts | Artifacts, Kernel |
| Sandbox | Sandbox, Kernel |
| Feed | Feed, Kernel |
| Application | Application, Research, Artifacts, Feed, Kernel |
| Tracking integration | Tracking integration, Application, Kernel |
| Storage | Storage, Kernel |
| Surface | any component; its independent layer classification still applies |

Application code enters a business component only through its declared
`facade.py` or `ports/**` entrypoint. This is the executable form of “one stable
public facade”; it prevents a new use case from depending on internal services.
Sandbox's stable component facade remains deliberately deferred until a narrow
sandbox use case reveals its real contract.

## Layer law

The initial layer mapping is deliberately honest about mixed directories:

| Layer | Representative paths |
|---|---|
| foundation | `kernel/**` |
| port | `kernel/ports/**`, `application/ports/**`, `sandbox/sandbox_backend.py` |
| domain | `research_core/domain/**`, pure artifact/feed policy files |
| application | component services, `application/**`, legacy Surface orchestration still being migrated |
| adapter | `mlflow/**`, concrete storage/blob code, sandbox provider drivers, client/runtime adapters |
| delivery | ordinary `surface/**` HTTP/MCP/auth/serialization code |
| bootstrap | Surface composition/config/control wiring, the HTTP process launcher, sandbox driver registration |

`object_storage/service.py` is a notable override: it owns versioning, TTL,
deduplication, lifecycle events, concurrency, and reclamation policy, so it is
Storage-component **application** code, not a provider adapter. Its physical
move is deferred. Conversely, `object_storage/{blobs,s3_blobs,s3_object_store}`
are adapters implementing kernel-owned storage ports.

Imports must point inward:

- foundation -> foundation;
- port -> port/foundation;
- domain -> domain/port/foundation;
- application -> application/domain/port/foundation;
- adapter -> adapter/application/domain/port/foundation;
- delivery -> delivery/application/port/foundation;
- bootstrap -> any layer.

Nothing except bootstrap may import bootstrap, and no non-delivery layer may
import delivery. `LAYER_EXCEPTIONS` contains exact importer/target pairs for
unrelated legacy Surface seams plus the named Feed-to-unfurl seam. It contains
no wildcard. Fixed pairs must be deleted, while new pairs fail immediately.

## Ports and adapters

Research/application workflows depend on an `ExperimentTracking` port, not on
MLflow. `CentralMlflowService` implements it. The port distinguishes logging,
control, and readback capabilities so tracking-only and server-only deployments
retain their current behavior.

Artifacts, Feed, cleanup, and storage-ledger policy depend on narrow blob/object
ports owned by Kernel. Local and S3 implementations remain under
`object_storage` as replaceable adapters. Old import paths may re-export the
same symbols for compatibility, but do not own their definitions.

These are dependency changes, not service extraction: everything still runs in
one brain process and shares the existing transaction/event ledger.

## Cross-plane law

Brain code may import pure `merv.shared` contracts but never `merv.proxy`.
Proxy code may import only the standard library, `merv.proxy`, and
`merv.shared`. Shared code imports only the standard library and itself. The
login client ships in the slim bundle and imports only the standard library and
`merv.shared`, never `merv.brain`.

## Executable ratchets

`tests/structure/test_module_boundaries.py` AST-scans top-level and
function-local imports, classifies every brain file twice, enforces both laws,
checks component-owned SQL, and rejects stale table entries and stale exception
pairs. SQL may name only tables owned by the file's component, Kernel tables,
or tables behind a ratified component dependency.

Sandbox provider neutrality is enforced separately: services do not dispatch
on provider-name literals. Capability flags and the typed `SandboxDriver` /
`SandboxManagementTransport` contracts express provider differences; lazy
provider descriptors form the composition registry; the shared offline driver
conformance suite applies to every registered implementation.

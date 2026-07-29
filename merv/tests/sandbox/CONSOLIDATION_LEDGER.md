# Sandbox test consolidation ledger

Baseline: 35 dedicated files and 557 collected cases. The implementation
target is at most 24 files and 450 cases; 557 remains the non-increase ceiling.

| Removed or consolidated coverage | Retained or replacement coverage |
| --- | --- |
| Decomposition tests that pinned `Facade`, `Runtime`, query, and view files | Architecture tests now assert the single `SandboxEngine` entry point, forbidden reach-through, and narrow I/O boundaries |
| Nine inherited duplicate `merv_run` cases | The shared harness is a mixin; the original unique script and metadata cases remain |
| Separate lifecycle-reducer and reaper-recovery files | Cleanup, chaos/restart, service lifecycle, expiry, receipt-ordering, and release scenarios exercise the same public transitions |
| Per-provider copies of the shared driver contract | One backend conformance suite plus each provider's unique routing, availability, authentication, and failure behavior |
| Provider catalog/config value snapshots and HTTP-client mechanics | Quota/catalog behavior and provider-specific backend contract/failure tests |
| Standalone transcript-cache and VM-bootstrap decomposition tests | Terminal cursor/UTF-8 framing, VM SSH transport, recorder, management-key, and end-to-end transcript scenarios |
| Obsolete caller-private-key minting tests | Management-key custody, caller public-key contract, secret non-disclosure, and post-boot retry coverage |
| Sandbox-local MLflow metrics archive tests | Moved unchanged to Application ownership |
| Lambda copies of invariant VM behavior | Shared VM/SSH behavior plus Lambda-only procurement, routing, and availability tests |

Final result: 24 dedicated files and 446 collected cases. This removes 111
cases from the 557-case baseline, stays below the 450-case target, and does not
increase the suite.

Final verification:

```text
pytest --collect-only -q merv/tests/sandbox  # 446 collected
pytest -q merv/tests/sandbox                 # 441 passed, 5 skipped
pytest -q merv/tests/application merv/tests/structure  # 289 passed
pytest -q merv/tests/surface                 # 482 passed
```

# Research Plugin Improvement Progress

Date started: 2026-07-02

## Scope

Work through the hands-on improvement requests from
`improvement_requests/2026-07-02_research_plugin_improvement_requests.md`,
making small, verified commits inside the Research Plugin repo.

## Batch 1: storage file helpers

Status: complete

Request addressed:

- Make storage easier to use end-to-end.

Implementation notes:

- Added `storage.upload_file` as a data-plane helper that hashes a local file,
  registers the storage object, streams bytes to the presigned upload target,
  and completes the ledger object.
- Added `storage.download_file` as a data-plane helper that resolves a storage
  object, downloads to a temp file, verifies sha256 and size, then atomically
  replaces the destination.
- Kept existing low-level `storage.put_object`, `storage.complete_upload`, and
  `storage.resolve` primitives for hosted/control-plane flows.
- Relative helper paths resolve against the project repo root in local mode.

Verification:

- `PYTHONPATH=. python -m unittest tests.storage.test_storage_ledger -v`
- `PYTHONPATH=. python -m unittest tests.surface.test_tool_contracts tests.surface.test_storage_http -v`
- `PYTHONPATH=. python -m unittest tests.surface.test_local_shipping.LocalShippingTest.test_mcp_launcher_uses_current_repo_for_state_and_resources -v`
- `PYTHONPATH=. python -m unittest tests.structure.test_plane_layout.ToolPlanePartitionTest tests.storage.test_storage_ledger tests.surface.test_storage_http tests.surface.test_tool_contracts -v`
- `PYTHONPATH=. python -m unittest discover -s tests -v` (841 tests, 25 skipped)

Follow-up candidates:

- Surface storage object associations more clearly in experiment state.
- Add sandbox output retention helpers that can choose storage automatically for
  large files.

## Batch 2: batch resource association

Status: complete

Request addressed:

- Batch resource association.

Implementation notes:

- Added `resource.associate_batch` as a data-plane helper over the existing
  `resource.associate` path.
- Rows are applied in order and preserve the same role validation,
  gated-artifact byte capture, and attempt scoping as single associations.
- Added split-mode daemon support so the tool is served anywhere
  `resource.associate` is served.

Verification:

- `PYTHONPATH=. python -m unittest tests.workflow.test_workflow_gates.WorkflowGateTest.test_resource_associate_batch_satisfies_results_gate -v`
- `PYTHONPATH=. python -m unittest tests.surface.test_tool_contracts tests.structure.test_plane_layout.ToolPlanePartitionTest -v`
- `PYTHONPATH=. python -m unittest tests.surface.test_split_mode_smoke.DaemonResourceForwardingTest.test_daemon_catalog_only_advertises_implemented_data_tools -v`
- `PYTHONPATH=. python -m unittest discover -s tests -v` (846 tests, 25 skipped)

## Batch 3: gated artifact preflight lint

Status: complete

Request addressed:

- Preflight linter for gated artifacts.

Implementation notes:

- Added `resource.validate` as a data-plane helper that reads the current local
  repo file without registering or associating it.
- The validator reports file/path errors, gated-role byte caps, required plan
  sections, report structure and figure availability, and graph envelope
  problems before transition gates are attempted.
- Wired the tool through local mode and split-mode daemon routing, with docs
  updates for the MCP contract and control/data-plane split.

Verification:

- `PYTHONPATH=. python -m unittest tests.sandbox.test_resource_artifact_validation -v`
- `PYTHONPATH=. python -m unittest tests.workflow.test_workflow_gates.WorkflowGateTest.test_resource_validate_preflights_plan_before_association -v`
- `PYTHONPATH=. python -m unittest tests.surface.test_tool_contracts tests.structure.test_plane_layout.ToolPlanePartitionTest -v`
- `PYTHONPATH=. python -m unittest tests.surface.test_split_mode_smoke.DaemonResourceForwardingTest.test_resource_validate_reads_local_file_without_control_mutation tests.surface.test_split_mode_smoke.DaemonResourceForwardingTest.test_daemon_catalog_only_advertises_implemented_data_tools -v`
- `PYTHONPATH=. python -m unittest discover -s tests -v` (854 tests, 25 skipped)

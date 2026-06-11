# Graph Report - .  (2026-06-10)

## Corpus Check
- 169 files · ~122,839 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1848 nodes · 4312 edges · 92 communities (74 shown, 18 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 496 edges (avg confidence: 0.53)
- Token cost: 12,800 input · 3,900 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Backend Daemon Service|Backend Daemon Service]]
- [[_COMMUNITY_MCP Server Layer|MCP Server Layer]]
- [[_COMMUNITY_Modal Sandbox Backend|Modal Sandbox Backend]]
- [[_COMMUNITY_Sandbox Service Tests|Sandbox Service Tests]]
- [[_COMMUNITY_Research Workflow Docs|Research Workflow Docs]]
- [[_COMMUNITY_HTTP API Server|HTTP API Server]]
- [[_COMMUNITY_Modal Backend Tests|Modal Backend Tests]]
- [[_COMMUNITY_Lambda Labs Backend|Lambda Labs Backend]]
- [[_COMMUNITY_App Entry Point|App Entry Point]]
- [[_COMMUNITY_Backend Utilities|Backend Utilities]]
- [[_COMMUNITY_Architecture & Design Review|Architecture & Design Review]]
- [[_COMMUNITY_Modal Sandbox Implementation|Modal Sandbox Implementation]]
- [[_COMMUNITY_API Contracts & Models|API Contracts & Models]]
- [[_COMMUNITY_Workflow & Resource Services|Workflow & Resource Services]]
- [[_COMMUNITY_Lambda Labs Config & Sandbox|Lambda Labs Config & Sandbox]]
- [[_COMMUNITY_Execution Error Types|Execution Error Types]]
- [[_COMMUNITY_Resource Service|Resource Service]]
- [[_COMMUNITY_Cloud Provider Config|Cloud Provider Config]]
- [[_COMMUNITY_Claims & Pager UI|Claims & Pager UI]]
- [[_COMMUNITY_Add Resource UI|Add Resource UI]]
- [[_COMMUNITY_Experiment Sync Indicator|Experiment Sync Indicator]]
- [[_COMMUNITY_Fake Sandbox Backend|Fake Sandbox Backend]]
- [[_COMMUNITY_Visual DAG Claim Graph|Visual DAG Claim Graph]]
- [[_COMMUNITY_Tool Call State|Tool Call State]]
- [[_COMMUNITY_Claim Detail & Project UI|Claim Detail & Project UI]]
- [[_COMMUNITY_State Store SQLite|State Store SQLite]]
- [[_COMMUNITY_Lambda Labs API Client|Lambda Labs API Client]]
- [[_COMMUNITY_Event Timeline UI|Event Timeline UI]]
- [[_COMMUNITY_Experiment Service & Workflow Gates|Experiment Service & Workflow Gates]]
- [[_COMMUNITY_File Icon Component|File Icon Component]]
- [[_COMMUNITY_Workflow Service|Workflow Service]]
- [[_COMMUNITY_Activity Logger|Activity Logger]]
- [[_COMMUNITY_Sandbox Connection|Sandbox Connection]]
- [[_COMMUNITY_Sandbox Conn File Sync|Sandbox Conn File Sync]]
- [[_COMMUNITY_Lambda Labs Catalog|Lambda Labs Catalog]]
- [[_COMMUNITY_Sandbox Provisioning|Sandbox Provisioning]]
- [[_COMMUNITY_Review Service|Review Service]]
- [[_COMMUNITY_JSON View Component|JSON View Component]]
- [[_COMMUNITY_UI Package Dependencies|UI Package Dependencies]]
- [[_COMMUNITY_Local Shipping Tests|Local Shipping Tests]]
- [[_COMMUNITY_Resource Content View|Resource Content View]]
- [[_COMMUNITY_SSH Rsync Execution|SSH Rsync Execution]]
- [[_COMMUNITY_Modal Transcript Reading|Modal Transcript Reading]]
- [[_COMMUNITY_Sandbox Service Core|Sandbox Service Core]]
- [[_COMMUNITY_Sandbox Cleanup|Sandbox Cleanup]]
- [[_COMMUNITY_Code & File Renderer|Code & File Renderer]]
- [[_COMMUNITY_Sandbox Provision Job|Sandbox Provision Job]]
- [[_COMMUNITY_Compute Service|Compute Service]]
- [[_COMMUNITY_Sidebar Navigation|Sidebar Navigation]]
- [[_COMMUNITY_Activity Page|Activity Page]]
- [[_COMMUNITY_HTTP API Tests|HTTP API Tests]]
- [[_COMMUNITY_Graphify Add & Watch|Graphify Add & Watch]]
- [[_COMMUNITY_Rsync Binary|Rsync Binary]]
- [[_COMMUNITY_Service Dependency Graph|Service Dependency Graph]]
- [[_COMMUNITY_SSH Rsync Result|SSH Rsync Result]]
- [[_COMMUNITY_Modal Config|Modal Config]]
- [[_COMMUNITY_Dashboard Tunnel|Dashboard Tunnel]]
- [[_COMMUNITY_Sync Details Modal|Sync Details Modal]]
- [[_COMMUNITY_Sandbox Terminal UI|Sandbox Terminal UI]]
- [[_COMMUNITY_Tool Call Timestamps|Tool Call Timestamps]]
- [[_COMMUNITY_Compute Service Protocol|Compute Service Protocol]]
- [[_COMMUNITY_Project Service|Project Service]]
- [[_COMMUNITY_Terminal Log Renderer|Terminal Log Renderer]]
- [[_COMMUNITY_Graphify Extraction Spec|Graphify Extraction Spec]]
- [[_COMMUNITY_Workflow Slim Tests|Workflow Slim Tests]]
- [[_COMMUNITY_Claude Plugin Metadata|Claude Plugin Metadata]]
- [[_COMMUNITY_GPU Usage Metrics|GPU Usage Metrics]]
- [[_COMMUNITY_Dev HTTP Reload Script|Dev HTTP Reload Script]]
- [[_COMMUNITY_Activity Log Tests|Activity Log Tests]]
- [[_COMMUNITY_Tool Call Store Tests|Tool Call Store Tests]]
- [[_COMMUNITY_Lambda Path Utilities|Lambda Path Utilities]]
- [[_COMMUNITY_Sandbox Time Utilities|Sandbox Time Utilities]]
- [[_COMMUNITY_Activity Cap Result|Activity Cap Result]]
- [[_COMMUNITY_Lambda User Data Build|Lambda User Data Build]]
- [[_COMMUNITY_Fake Process Test Helper|Fake Process Test Helper]]
- [[_COMMUNITY_MCP Server Entry Point|MCP Server Entry Point]]
- [[_COMMUNITY_GitHub Merge & Clone|GitHub Merge & Clone]]
- [[_COMMUNITY_Activity Logger Init|Activity Logger Init]]
- [[_COMMUNITY_Modal Fake Secret|Modal Fake Secret]]
- [[_COMMUNITY_Plugin Skills Tests|Plugin Skills Tests]]
- [[_COMMUNITY_Graphify Watch Mode|Graphify Watch Mode]]
- [[_COMMUNITY_Backends Init|Backends Init]]
- [[_COMMUNITY_Execution Bootstrap|Execution Bootstrap]]
- [[_COMMUNITY_Whisper Transcription|Whisper Transcription]]
- [[_COMMUNITY_App Icon Asset|App Icon Asset]]
- [[_COMMUNITY_Neo4j Export|Neo4j Export]]
- [[_COMMUNITY_Wiki Export|Wiki Export]]
- [[_COMMUNITY_Graphify Skill Entry|Graphify Skill Entry]]
- [[_COMMUNITY_Git Commit Hook|Git Commit Hook]]
- [[_COMMUNITY_Python Requirements|Python Requirements]]

## God Nodes (most connected - your core abstractions)
1. `ResearchPluginApp` - 77 edges
2. `SandboxService` - 76 edges
3. `ValidationError` - 68 edges
4. `SandboxServiceTest` - 66 edges
5. `ProjectRouter` - 59 edges
6. `BackendUnavailableError` - 56 edges
7. `BackendValidationError` - 55 edges
8. `NotFoundError` - 54 edges
9. `ResearchHttpApi` - 45 edges
10. `now_iso()` - 41 edges

## Surprising Connections (you probably didn't know these)
- `Graphify MCP Server (--mcp flag)` --semantically_similar_to--> `Research Plugin MCP Stdio Proxy`  [INFERRED] [semantically similar]
  .codex/skills/graphify/references/exports.md → research_plugin/README.md
- `Native CLAUDE.md Integration` --semantically_similar_to--> `Graphify Integration Section in AGENTS.md`  [INFERRED] [semantically similar]
  .codex/skills/graphify/references/hooks.md → AGENTS.md
- `Research State UI Entry Point (index.html)` --conceptually_related_to--> `Frontend Redesign Objectives`  [INFERRED]
  research_state_ui/index.html → research_plugin/docs/FRONTEND_REDESIGN_OBJECTIVES.md
- `CompletedProcess` --uses--> `ResearchPluginApp`  [INFERRED]
  research_plugin/scripts/smoke_modal_sandbox.py → research_plugin/backend/app.py
- `_ProvisionJob` --uses--> `SandboxConnFiles`  [INFERRED]
  research_plugin/backend/services/sandboxes.py → research_plugin/backend/services/sandbox_conn.py

## Import Cycles
- 1-file cycle: `research_plugin/backend/http_api.py -> research_plugin/backend/http_api.py`
- 1-file cycle: `research_plugin/backend/services/sandboxes.py -> research_plugin/backend/services/sandboxes.py`
- 1-file cycle: `research_plugin/backend/services/sandbox_support.py -> research_plugin/backend/services/sandbox_support.py`
- 1-file cycle: `research_plugin/backend/state/tool_calls.py -> research_plugin/backend/state/tool_calls.py`
- 3-file cycle: `research_plugin/backend/services/__init__.py -> research_plugin/backend/services/workflow.py -> research_plugin/backend/services/sandboxes.py -> research_plugin/backend/services/__init__.py`

## Hyperedges (group relationships)
- **Sandbox Observability Stack (SSH + MLflow + TensorBoard + Transcript)** — readme_ssh_dispatcher, readme_mlflow_tracking, readme_tensorboard, modal_sandboxes_rec_sh [INFERRED 0.85]
- **Review Gate Workflow (design + experiment review with capability tokens)** — readme_reviewer_handoff, design_review_agent, experiment_review_agent, docs_architecture_reviewer_identity [EXTRACTED 1.00]
- **Graphify Extraction Pipeline (AST + Semantic + Merge)** — graphify_skill_ast_extraction, graphify_skill_semantic_extraction, graphify_skill_extraction_cache [EXTRACTED 1.00]
- **Review Gate Enforcement Flow (request → capability → session → submit)** — docs_reviewer_capability_token, docs_reviewer_independence_boundary, docs_target_snapshot, docs_review_gate_substate [EXTRACTED 0.95]
- **Experiment Lifecycle Gate System** — docs_experiment_fsm, docs_plan_gate, docs_result_sync_gate, docs_claim_update_gate, docs_allowed_transitions [EXTRACTED 0.95]
- **Control/Data Plane Seam (sync session + lease + direction policy)** — docs_sync_session_contract, docs_lease_authority, docs_direction_policy, docs_local_data_plane_daemon, docs_cloud_control_plane [EXTRACTED 0.95]

## Communities (92 total, 18 thin omitted)

### Community 0 - "Backend Daemon Service"
Cohesion: 0.05
Nodes (41): clear_marker(), DaemonInfo, discover_daemon_url(), marker_path(), Helpers for locating a running research_plugin HTTP daemon.  Other processes in, Return the daemon URL from env or the repo marker, or None., Write the daemon marker. Best-effort: returns the path even if write fails., Remove the daemon marker. Idempotent; ignores missing/permission errors. (+33 more)

### Community 1 - "MCP Server Layer"
Cohesion: 0.05
Nodes (38): HTTPError, clear_marker(), DaemonInfo, discover_daemon_url(), marker_path(), Helpers for locating a running research_plugin HTTP daemon.  Other processes in, Return the daemon URL from env or the repo marker, or None., Write the daemon marker. Best-effort: returns the path even if write fails. (+30 more)

### Community 2 - "Modal Sandbox Backend"
Cohesion: 0.05
Nodes (26): ModalSandboxBackend, Path, SshRsyncResult, CompletedProcess, hr(), main(), One-off live smoke test for the Lambda-default hardware-selection flow.  Drives, Safety net: terminate any rp-* instance we may have created. (+18 more)

### Community 3 - "Sandbox Service Tests"
Cohesion: 0.09
Nodes (3): A completed-command transcript block in the rec.sh marker format., Flip the fake backend into Lambda-style bundled-hardware behavior., SandboxServiceTest

### Community 4 - "Research Workflow Docs"
Cohesion: 0.06
Nodes (54): Design Review Skill, allowed_transitions (experiment.get_state precondition hints), artifacts_to_keep Sync Path (large artifact exception), Claim Update Gate (evidence + review requirement), Cloud Control Plane, Control/Data Plane Split Proposal, Daemon-First Process Topology, Design Reviewer Contract (+46 more)

### Community 5 - "HTTP API Server"
Cohesion: 0.08
Nodes (19): HTTP view helpers over ResearchPluginApp. Domain logic stays in services., ResearchHttpApi, local_experiment_sync_dir(), Provider-neutral remote directory contract for SSH sandboxes., Filesystem-safe directory name for an experiment id., safe_experiment_dirname(), sync_hint(), Any (+11 more)

### Community 6 - "Modal Backend Tests"
Cohesion: 0.05
Nodes (11): SandboxRequest, FakeEncryptedTunnel, FakeImage, FakeModal, FakeProcess, FakeSandbox, FakeSandboxClass, FakeTunnel (+3 more)

### Community 7 - "Lambda Labs Backend"
Cohesion: 0.09
Nodes (8): LambdaLabsSandboxBackend, FakeLambdaSandboxClient, FakeSshRunner, LambdaEnvironmentTest, LambdaMetricsTest, LambdaSelectionTest, LambdaTranscriptTest, Records ssh invocations and returns a canned CompletedProcess.

### Community 8 - "App Entry Point"
Cohesion: 0.10
Nodes (27): _contract_error_message(), Application composition root and MCP tool facade., Best-effort: stop background provisioning jobs and the sync poller., Bridge backend emit-style logging and ActivityLogger., Composes isolated components behind tool-call contracts., ResearchPluginApp, ToolSpec, create_fastapi_app() (+19 more)

### Community 9 - "Backend Utilities"
Cohesion: 0.12
Nodes (23): new_id(), now_iso(), Cross-cutting helpers for the Research Plugin backend.  Holds the small, depende, Return an opaque id of the form ``"<prefix>_<12-hex-chars>"``., Return the current UTC instant as an ISO-8601 string (``…Z``)., Any, StateStore, Any (+15 more)

### Community 10 - "Architecture & Design Review"
Cohesion: 0.07
Nodes (38): Design Review Subagent, Experiment Plan Spine (Summary/Objective/Evaluation), Design Review Verdict (pass/needs_changes/fail), Research Plugin Architecture Document, Research Plugin Simplified Data Model (Project/Claim/Experiment/Resource/Review/Event), Plugin Architecture Design Thesis, Experiment State Machine, MCP Mutation Model (all state changes via MCP tools) (+30 more)

### Community 11 - "Modal Sandbox Implementation"
Cohesion: 0.12
Nodes (13): _call(), ModalSandboxBackend, Best-effort lookup of a sandbox we created for this experiment by name., Best-effort re-read of the encrypted dashboard tunnels for a live sandbox., Re-read the live SSH tunnel endpoint for an existing sandbox.          Lets the, Static catalog: Modal lets the agent set gpu/cpu/memory independently., Build Modal sandbox secrets from the daemon environment.          The backend ha, Read the encrypted tunnel URLs for the dashboard ports.          Modal tunnels f (+5 more)

### Community 12 - "API Contracts & Models"
Cohesion: 0.11
Nodes (33): ClaimCreateInput, ClaimListInput, ClaimUpdateInput, ContractModel, EmptyInput, ExperimentCreateInput, ExperimentGetStateInput, ExperimentListInput (+25 more)

### Community 13 - "Workflow & Resource Services"
Cohesion: 0.11
Nodes (17): WorkflowError, Any, StateStore, ExperimentService, StateStore, ResourceService, ReviewService, SandboxService (+9 more)

### Community 14 - "Lambda Labs Config & Sandbox"
Cohesion: 0.10
Nodes (18): LambdaSandboxConfig, build_lambda_labs_sandbox_backend(), _call(), _int_or_zero(), LambdaLabsSandboxBackend, Sample live VM usage (CPU/RAM/GPU) via an unrecorded SSH exec.          Runs the, Dashboard ports reachable only from inside the VM.          The registry turns t, Validate the SKU + capacity and pick a region; return (region, specs). (+10 more)

### Community 15 - "Execution Error Types"
Cohesion: 0.13
Nodes (21): BackendPermissionError, ExecutionBackendError, Exceptions for the job-runtime subsystem.  Defined inside the subpackage so job-, Base error for execution backends., Caller-supplied job spec or environment violates execution policy., build_sandbox_backend(), Backend-neutral sandbox-execution subsystem.  The runtime contract (SandboxBacke, Select and construct the configured sandbox backend.      Backend name comes fro (+13 more)

### Community 16 - "Resource Service"
Cohesion: 0.20
Nodes (11): NotFoundError, Any, Connection, Path, Row, _content_sha256(), List registered resources with optional filters + pagination.          ``compact, Manages one-file-one-resource observation and associations. (+3 more)

### Community 17 - "Cloud Provider Config"
Cohesion: 0.15
Nodes (23): BackendValidationError, Caller-supplied job spec or backend hints are malformed., load_lambda_env_file(), Load Lambda credentials/settings from the configured plugin env file., _absolute_posix_path(), _bool_hint(), _env_int(), _env_non_negative_int() (+15 more)

### Community 18 - "Claims & Pager UI"
Cohesion: 0.09
Nodes (18): AGAINST_STATUSES, ClaimEntry(), Claims(), CONFIDENCE_LEVELS, LIVE_STATUSES, SUPPORT_STATUSES, TABS, Experiments() (+10 more)

### Community 19 - "Add Resource UI"
Cohesion: 0.08
Nodes (14): KINDS, ROLES, GATE_STATES, STAGES, TERMINAL, GateBanner(), prettyGate(), ExperimentDetail() (+6 more)

### Community 20 - "Experiment Sync Indicator"
Cohesion: 0.10
Nodes (18): ACTIVE_STATUSES, deriveRow(), ExperimentSyncIndicator(), fmtAgo(), num(), PULL_EVENTS, SYNC_EVENTS, countOf() (+10 more)

### Community 21 - "Fake Sandbox Backend"
Cohesion: 0.08
Nodes (11): FakeSandboxBackend, Deterministic stand-in for ModalSandboxBackend.      Tracks acquired sandboxes,, A small, deterministic, cheapest-first SKU menu (Lambda-shaped)., Filterable, cheapest-first menu — only bound when selection is on., Simulate Modal reaping a sandbox (timeout / crash)., Simulate Modal relocating a live sandbox's SSH tunnel., Simulate Modal relocating a live sandbox's encrypted dashboard tunnels., OnCreated (+3 more)

### Community 22 - "Visual DAG Claim Graph"
Cohesion: 0.13
Nodes (21): DagNode(), daysBetween(), formatDate(), HoverInfo(), LAYER_LABELS, STATUS_FILL, VisualDag(), wrapLabel() (+13 more)

### Community 23 - "Tool Call State"
Cohesion: 0.13
Nodes (12): Any, Connection, Path, Per-tool aggregate + a filtered/sorted slice of individual calls.          Filte, Return one call's full record, with args/result parsed back to JSON., Drop all recorded calls. Returns how many were removed., A connection that commits on success and always closes.          `with sqlite3.c, Serialize a payload for storage. Returns (text, truncated, full_chars). (+4 more)

### Community 24 - "Claim Detail & Project UI"
Cohesion: 0.14
Nodes (12): ClaimDetail(), CreateProject(), CATEGORIES, Events(), fmtDate(), ProjectCard(), Projects(), Reviews() (+4 more)

### Community 25 - "State Store SQLite"
Cohesion: 0.15
Nodes (8): Connection, Path, Connection, Owns SQLite connections and basic persistence helpers., Drop columns that no longer appear in the live schema.          Requires SQLite, Re-key `resources` uniqueness from `path` to `(project_id, path)`.          The, StateStore, StoreMigrationTest

### Community 26 - "Lambda Labs API Client"
Cohesion: 0.18
Nodes (10): BackendUnavailableError, The selected backend cannot be reached or initialized., LambdaCloudClient, Small stdlib client for the Lambda Cloud API., LambdaCloudConfig, Lambda Labs Cloud support., Tail the rec.sh transcript live over SSH.          Uses the registry's stored en, LambdaCloudConfig (+2 more)

### Community 27 - "Event Timeline UI"
Cohesion: 0.09
Nodes (3): ProjectSwitcher(), ReviewCard(), shortDateTime()

### Community 28 - "Experiment Service & Workflow Gates"
Cohesion: 0.17
Nodes (5): _normalize_heading(), plan_sections_missing(), Lowercase, expand '&' to 'and', collapse to space-separated words., Return the canonical names of REQUIRED plan sections that are absent or     empt, WorkflowGateTest

### Community 29 - "File Icon Component"
Cohesion: 0.10
Nodes (8): MAP, sz, buildTree(), FileTree(), matchesQuery(), sortTree(), topLevelFolderPaths(), TreeNode()

### Community 30 - "Workflow Service"
Cohesion: 0.21
Nodes (9): Any, Collapse the sandbox row(s) to 'is there an active one, and if so what'., Computes status and next actions from durable state., Agent/MCP-facing status_and_next: the full computation, slim output.          Ba, Project the rich status_and_next result down to what the agent needs., _sandbox_summary(), _slim_experiment(), slim_status_and_next() (+1 more)

### Community 31 - "Activity Logger"
Cohesion: 0.17
Nodes (14): BaseException, Any, ActivityLogger, effective_source(), is_event_ok(), jsonable(), payload_chars(), Log an exception event with full traceback.          Both the HTTP server and th (+6 more)

### Community 32 - "Sandbox Connection"
Cohesion: 0.15
Nodes (11): Event, Any, Path, ActivityLogger, SandboxBackend, SshRsyncSyncer, StateStore, Drop the conn file so `sbx` fails loudly for a dead sandbox. (+3 more)

### Community 33 - "Sandbox Conn File Sync"
Cohesion: 0.14
Nodes (17): Exception, Per-experiment SSH key + dispatcher + connection-file plumbing.  ``SandboxConnFi, Refresh the per-experiment conn file and return the short command.          Retu, _Canceled, iso_after(), parse_terminal_markers(), _ProvisionJob, Pure helpers, constants, and value types for the sandbox service.  Everything he (+9 more)

### Community 34 - "Lambda Labs Catalog"
Cohesion: 0.16
Nodes (18): find_option(), _gpu_label(), _int_or_zero(), _norm(), Shape Lambda Cloud `/instance-types` data into selection catalogs.  One place ow, Flatten the rich catalog into a compact menu the agent chooses from.      Sorted, Return the flat menu entry for one instance type, or None if absent., Best-effort short GPU label, e.g. 'H100' from 'H100 (80 GB SXM5)'. (+10 more)

### Community 35 - "Sandbox Provisioning"
Cohesion: 0.19
Nodes (6): Path, ProvisionedSandbox, env_float(), Read-only poll target. Never provisions; reconciles stale state., Read the experiment's terminal transcript.          Supports incremental polling, Sample live in-container usage (CPU/RAM/GPU) for a running sandbox.          Rea

### Community 36 - "Review Service"
Cohesion: 0.24
Nodes (4): PermissionDeniedError, Any, Owns review gates and capability-scoped reviewer sessions., ReviewService

### Community 37 - "JSON View Component"
Cohesion: 0.14
Nodes (13): Node(), typeOf(), CALL_COLS, CallDetail(), CallRow(), Debug(), fmtChars(), fmtNum() (+5 more)

### Community 38 - "UI Package Dependencies"
Cohesion: 0.10
Nodes (19): dependencies, prism-react-renderer, react, react-dom, react-markdown, react-router-dom, remark-gfm, zustand (+11 more)

### Community 39 - "Local Shipping Tests"
Cohesion: 0.17
Nodes (3): LocalShippingTest, Spin up the HTTP daemon for the research repo on a free port., Wait for the daemon to print its bound URL and write the marker.

### Community 40 - "Resource Content View"
Cohesion: 0.13
Nodes (10): filenameOf(), PdfView(), humanBytes(), isPdfPath(), ResourceContentView(), formatBytes(), KINDS, PreviewPanel() (+2 more)

### Community 41 - "SSH Rsync Execution"
Cohesion: 0.20
Nodes (11): _count_changed(), _probe_version(), Provider-neutral rsync transfer for SSH sandboxes., Locate the best available rsync, preferring a modern (>= 3.0) build.      Resolu, resolve_rsync(), _rsync_too_old_error(), SshRsyncSyncer, CompletedProcess (+3 more)

### Community 42 - "Modal Transcript Reading"
Cohesion: 0.19
Nodes (14): Modal sandbox backend: procure SSH-wired sandboxes, no job protocol.  Implements, Sample live in-container usage (CPU/RAM/GPU) via a read-only exec.          Retu, _transcript_rel_path(), _write_file_layer(), ensure_remote_dir(), exec_checked(), maybe_await(), Small helpers for interacting with Modal sandboxes. (+6 more)

### Community 43 - "Sandbox Service Core"
Cohesion: 0.14
Nodes (8): Inverse of _mark_experiment_running, for a sandbox reaped at expiry.          Wi, Ask the backend what hardware can be requested (best-effort shape).          Bac, Describe the hardware the agent can request from the active backend.          La, All sandbox rows for a project (most-recent first)., Full backend health payload (the slim ``health`` tool trims this)., Owns sandbox persistence and delegates provisioning to a backend., Signal all in-flight provisioning jobs to stop (best-effort)., SandboxService

### Community 44 - "Sandbox Cleanup"
Cohesion: 0.22
Nodes (4): Any, Re-read a live sandbox's SSH tunnel and persist it if it moved.          Recover, Bring a row in line with reality. Read-only-safe (never provisions).          -, Best-effort terminate any sandbox tied to this experiment.          Covers both

### Community 45 - "Code & File Renderer"
Cohesion: 0.16
Nodes (7): EXT_TO_LANG, extOf(), FileRenderer(), isMarkdown(), PlanBody(), parsePlanSections(), SECTION_ROLES

### Community 46 - "Sandbox Provision Job"
Cohesion: 0.16
Nodes (7): _ProvisionJob, Any, SandboxRequest, encode_dashboards(), Re-read the encrypted dashboard tunnel URLs and persist if changed.          Com, Return the in-flight job for this experiment, or start a fresh one.          Ide, Background worker: sync → create → tunnel, updating the row per phase.

### Community 47 - "Compute Service"
Cohesion: 0.17
Nodes (3): ComputeService, FakeLambdaClient, LambdaAvailabilityTest

### Community 48 - "Sidebar Navigation"
Cohesion: 0.18
Nodes (12): fmtSyncedAgo(), NEXT_THEME_MODE, Sidebar(), selectResources(), selectStats(), apply(), effectiveTheme(), listeners (+4 more)

### Community 49 - "Activity Page"
Cohesion: 0.15
Nodes (6): EVENT_TABS, formatBytes(), JsonPane(), jsonSize(), SOURCE_TABS, STATUS_TABS

### Community 51 - "Graphify Add & Watch"
Cohesion: 0.14
Nodes (14): Graphify Add URL Command, graphify.ingest Module, Graphify Integration Section in AGENTS.md, Graph Query (BFS/DFS traversal), Incremental Graph Update (--update), Native CLAUDE.md Integration, BFS Graph Traversal, DFS Graph Traversal (+6 more)

### Community 52 - "Rsync Binary"
Cohesion: 0.19
Nodes (3): RsyncBinary, RsyncBinaryResolutionTest, SshRsyncSyncerTest

### Community 53 - "Service Dependency Graph"
Cohesion: 0.18
Nodes (8): PermissionService, StateStore, ExperimentService, PermissionService, StateStore, PermissionService, Small policy layer, intentionally separate from workflow and persistence., stat_result

### Community 54 - "SSH Rsync Result"
Cohesion: 0.21
Nodes (6): SshRsyncResult, SshRsyncResult, SshRsyncResult, FakeRsyncSyncer, FakeRsyncSyncer, The agent-facing `workflow.status_and_next` tool returns a slim projection; the

### Community 55 - "Modal Config"
Cohesion: 0.23
Nodes (8): ModalConfig, Modal sandbox execution backend., build_modal_sandbox_backend(), ModalConfig, ActivityHook, Path, ProvisionedSandbox, SandboxRequest

### Community 56 - "Dashboard Tunnel"
Cohesion: 0.19
Nodes (8): Popen, _dashboard_url_ready(), _DashboardTunnel, _free_local_port(), _is_local_dashboard_url(), Expose in-sandbox dashboards through daemon-owned SSH local forwards.          M, Reconciled sandbox row for an experiment, or None if none exists., A daemon-owned SSH local port-forward for one dashboard.

### Community 57 - "Sync Details Modal"
Cohesion: 0.26
Nodes (10): DIR_CHIP, DirRow(), ExperimentSyncDetailsModal(), fmtAgo(), num(), PULL_EVENTS, shortenPath(), shortError() (+2 more)

### Community 58 - "Sandbox Terminal UI"
Cohesion: 0.21
Nodes (5): fmtBytes(), fmtCores(), fmtMib(), PANEL_TABS, SandboxUsage()

### Community 59 - "Tool Call Timestamps"
Cohesion: 0.18
Nodes (8): datetime, Row, _parse_ts(), _percentile(), Full-fidelity tool-call recorder for the debug analyzer.  The activity log (`act, True when the requested window may extend past evicted history., PercentileTest, Tests for the full-fidelity tool-call store backing the debug analyzer.

### Community 60 - "Compute Service Protocol"
Cohesion: 0.24
Nodes (6): Protocol, Any, CompletedProcess, ComputeService, LambdaInstanceTypeClient, Compute-provider discovery helpers.

### Community 61 - "Project Service"
Cohesion: 0.36
Nodes (4): Any, StateStore, ProjectService, Owns project metadata.

### Community 62 - "Terminal Log Renderer"
Cohesion: 0.33
Nodes (8): applySgr(), FG, highlightJson(), looksJson(), parseAnsi(), renderOutput(), TermLine(), xterm256()

### Community 63 - "Graphify Extraction Spec"
Cohesion: 0.20
Nodes (10): Confidence Score Rubric, Hyperedge Rule (max 3 per chunk), Node ID Format Rule, Extraction Subagent Prompt Template, AST Structural Extraction (Part A), Community Detection and Clustering, Extraction Cache (semantic cache), God Nodes Analysis (+2 more)

### Community 65 - "Claude Plugin Metadata"
Cohesion: 0.22
Nodes (8): metadata, description, version, name, owner, name, plugins, $schema

### Community 66 - "GPU Usage Metrics"
Cohesion: 0.36
Nodes (8): _parse_gpu(), parse_metrics(), Shared live-usage sampler for SSH-accessible compute environments.  Both executi, Parse one `idx=.. util=.. used=.. total=.. name=..` GPU line., Turn `RPM key=value` sampler lines into a structured gauge dict., _to_float(), _to_int(), Any

### Community 67 - "Dev HTTP Reload Script"
Cohesion: 0.47
Nodes (8): Path, Popen, iter_watched_files(), main(), port_in_use(), server_command(), start_server(), stop_server()

### Community 68 - "Activity Log Tests"
Cohesion: 0.50
Nodes (3): ActivityLogger, Path, TailReadTest

### Community 70 - "Lambda Path Utilities"
Cohesion: 0.39
Nodes (6): _absolute_posix_path(), _first_env(), _is_under_path(), _positive_float(), _positive_int(), Configuration for Lambda Labs Cloud API and VM access.

### Community 71 - "Sandbox Time Utilities"
Cohesion: 0.25
Nodes (4): datetime, datetime, parse_iso(), Terminate every running sandbox whose expires_at deadline has passed.          I

### Community 72 - "Activity Cap Result"
Cohesion: 0.33
Nodes (4): cap_result(), Return a JSON-safe result capped to RESULT_LOG_MAX_BYTES.      Oversized results, CapResultTest, Tests for bounded activity-log reads and result payload capping.

### Community 75 - "MCP Server Entry Point"
Cohesion: 0.50
Nodes (3): RESEARCH_PLUGIN_DAEMON_URL, ${CLAUDE_PLUGIN_ROOT}/bin/research-plugin-mcp, research-plugin

### Community 76 - "GitHub Merge & Clone"
Cohesion: 0.67
Nodes (3): GitHub Repo Clone (graphify clone), Cross-Repo Graph Merge, Monorepo Multi-Subfolder Merge

## Knowledge Gaps
- **137 isolated node(s):** `$schema`, `name`, `name`, `description`, `version` (+132 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **18 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ResearchPluginApp` connect `App Entry Point` to `Backend Daemon Service`, `MCP Server Layer`, `Modal Sandbox Backend`, `Sandbox Service Tests`, `Workflow Slim Tests`, `HTTP API Server`, `Lambda Labs Backend`, `Lambda User Data Build`, `Fake Process Test Helper`, `API Contracts & Models`, `Experiment Service & Workflow Gates`, `Compute Service`, `HTTP API Tests`, `SSH Rsync Result`, `State Store SQLite`, `Compute Service Protocol`?**
  _High betweenness centrality (0.117) - this node is a cross-community bridge._
- **Why does `BackendUnavailableError` connect `Lambda Labs API Client` to `Sandbox Conn File Sync`, `Lambda Labs Catalog`, `Sandbox Provisioning`, `Modal Backend Tests`, `Lambda Labs Backend`, `Lambda User Data Build`, `Modal Transcript Reading`, `Modal Sandbox Implementation`, `Lambda Labs Config & Sandbox`, `Execution Error Types`, `Compute Service`, `Modal Fake Secret`, `Fake Sandbox Backend`, `Compute Service Protocol`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Why does `SandboxService` connect `Sandbox Service Core` to `Sandbox Connection`, `Sandbox Conn File Sync`, `Sandbox Provisioning`, `Sandbox Time Utilities`, `App Entry Point`, `Backend Utilities`, `Sandbox Cleanup`, `Workflow & Resource Services`, `Sandbox Provision Job`, `Dashboard Tunnel`, `Workflow Service`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Are the 58 inferred relationships involving `ResearchPluginApp` (e.g. with `ContractModel` and `ResearchPluginError`) actually correct?**
  _`ResearchPluginApp` has 58 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `SandboxService` (e.g. with `Any` and `ExperimentService`) actually correct?**
  _`SandboxService` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 27 inferred relationships involving `ValidationError` (e.g. with `ResearchPluginApp` and `ToolSpec`) actually correct?**
  _`ValidationError` has 27 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `SandboxServiceTest` (e.g. with `ResearchPluginApp` and `ResearchHttpApi`) actually correct?**
  _`SandboxServiceTest` has 8 INFERRED edges - model-reasoned connections that need verification._
"""Application composition root and MCP tool facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .dataplane import LocalDataPlaneWorker
from .execution import SandboxBackend, build_sandbox_backend
from .execution.ssh_rsync import SshRsyncSyncer
from .workspace import LocalWorkspace
from .services.claims import ClaimService
from .services.experiments import ExperimentService
from .services.feed import FeedService
from .services.permissions import PermissionService
from .services.project_overview import ProjectOverviewService
from .services.projects import ProjectService
from .services.resources import ResourceService
from .services.reviews import ReviewService
from .services.sandboxes import SandboxService
from .services.sandbox_mgmt_keys import LocalMgmtKeyStore
from .services.syntheses import SynthesisService
from .services.workflow import WorkflowService
from .state import (
    ActivityLogger,
    BaseStateStore,
    StateStore,
    ToolCallStore,
)
from .state.blobs import BlobStore, LocalDirBlobStore
from .observability import StructuredLogger
from .tool_facade import ToolDispatcher


class ResearchPluginApp:
    """Composes isolated components behind tool-call contracts."""

    def __init__(
        self,
        *,
        repo_root: Path,
        db_path: Path,
        execution_backend: SandboxBackend | None = None,
        rsync_syncer: SshRsyncSyncer | None = None,
        store: BaseStateStore | None = None,
        blobs: "BlobStore | None" = None,
        task_channel: Any | None = None,
    ) -> None:
        # The plane seam (cloud plan Phase 3): the record store knows nothing
        # about the checkout; local paths flow from the workspace and every
        # local-IO duty routes through the data-plane worker. This constructor
        # IS the local-mode composition — it binds both planes in one process.
        self.workspace = LocalWorkspace(repo_root=repo_root)
        # Store injection (cloud plan Phase 6): the dual-dialect contract
        # tests hand in a PostgresStateStore; absent that, local mode builds
        # its SQLite store at db_path exactly as before. The control profile
        # (Phase 8) injects a PostgresStateStore + S3BlobStore via the control
        # composition root rather than db_url plumbing through here.
        self.store = store if store is not None else StateStore(db_path=db_path)
        # Telemetry sinks are machine-local by construction: composition hands
        # them explicit paths (the control composition gets its own sinks).
        self.activity = ActivityLogger(repo_root=self.workspace.repo_root)
        # Full-fidelity tool-call recorder backing the debug analyzer. Isolated in
        # its own SQLite file so its churn never touches the state DB.
        self.tool_calls = ToolCallStore(
            db_path=self.workspace.research_dir / "tool_calls.sqlite"
        )
        # Structured cloud log stream (cloud plan Phase 9): one redacted JSON
        # line per tool call / HTTP request to stdout, in control mode only.
        # Dormant (disabled) in local mode, so behavior is byte-identical.
        self.structured_logger = StructuredLogger()
        self.permissions = PermissionService()
        # Content-addressed store for gated-artifact bytes (and figures and
        # parachute objects). Local mode roots it next to the state DB; the
        # control composition injects an S3BlobStore (Phase 8). Same protocol,
        # same contract tests, so the rest of the app is blob-impl-blind.
        self.blobs = blobs if blobs is not None else LocalDirBlobStore(
            root=self.workspace.research_dir / "blobs"
        )
        if execution_backend is None:
            execution_backend = build_sandbox_backend(
                repo_root=self.workspace.repo_root,
                activity=self._activity_hook,
            )
        self.execution_backend = execution_backend
        self.worker = LocalDataPlaneWorker(
            workspace=self.workspace,
            backend=execution_backend,
            rsync_syncer=rsync_syncer,
        )
        self.projects = ProjectService(store=self.store)
        self.claims = ClaimService(store=self.store)
        self.experiments = ExperimentService(
            store=self.store,
            blobs=self.blobs,
        )
        self.resources = ResourceService(
            store=self.store,
            permissions=self.permissions,
            workspace=self.workspace,
            blobs=self.blobs,
        )
        # One-time local upgrade: capture bytes for gated associations made
        # before byte capture existed (idempotent, skips present blobs).
        self.resources.backfill_gated_blobs()
        self.syntheses = SynthesisService(
            store=self.store,
            blobs=self.blobs,
        )
        self.project_overview = ProjectOverviewService(
            store=self.store,
            projects=self.projects,
            syntheses=self.syntheses,
        )
        self.reviews = ReviewService(
            store=self.store,
            permissions=self.permissions,
            experiments=self.experiments,
            syntheses=self.syntheses,
            blobs=self.blobs,
        )
        self.sandboxes = SandboxService(
            store=self.store,
            sandbox_backend=execution_backend,
            worker=self.worker,
            activity=self.activity,
            experiments=self.experiments,
            # Per-sandbox management keys (plan Phase 5): control-plane
            # custody — local mode roots them under .research_plugin/ beside
            # the rest of the control state.
            mgmt_keys=LocalMgmtKeyStore(
                root=self.workspace.research_dir / "mgmt_keys"
            ),
            metrics_archive=self.worker.metrics_archive,
            lease_client_id=self.worker.client_id(),
            # Decision 7's one shared blob store also holds parachute objects.
            blobs=self.blobs,
            # Split mode (Phase 8): the control composition injects an
            # HttpTaskChannel so control enqueues data-plane work to the daemon
            # over HTTP. None ⇒ the synchronous in-process channel (local mode).
            task_channel=task_channel,
        )
        self.workflow = WorkflowService(
            store=self.store,
            experiments=self.experiments,
            reviews=self.reviews,
            sandboxes=self.sandboxes,
            resources=self.resources,
            syntheses=self.syntheses,
        )
        # Feed (Feed_PRD.md) is a self-contained module: it owns its schema,
        # tools, HTTP routes, and UI, and nothing in the research workflow
        # depends on it. Constructed here purely as a composition-root wiring.
        self.feed = FeedService(
            store=self.store,
            workspace=self.workspace,
            blobs=self.blobs,
        )
        handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "workflow.status_and_next": self.workflow.status_and_next_agent,
            "project.create": self.projects.create,
            "project.update": self.projects.update,
            "project.get": self.projects.get,
            "project.current": self.project_overview.current_project,
            "project.list": self.projects.list_projects,
            "claim.create": self.claims.create,
            "claim.list": self.claims.list_claims,
            "claim.update": self.claims.update,
            "experiment.create": self.experiments.create,
            "experiment.list": self.experiments.list_experiments_agent,
            "experiment.get_state": self.experiments.get_state_agent,
            "experiment.transition": self.experiments.transition,
            "reflection.create": self.reflection_create,
            "reflection.get": self.reflection_get,
            "reflection.list": self.reflection_list,
            "reflection.transition": self.reflection_transition,
            "resource.register_file": self.resources.register_file,
            "resource.associate": self.resources.associate,
            "resource.delete": self.resources.delete,
            "resource.list": self.resources.list_resources,
            "resource.resolve": self.resources.resolve,
            "review.request": self.reviews.request,
            "review.start": self.reviews.start,
            "review.submit": self.reviews.submit,
            "review.status": self.reviews.status,
            "sandbox.request": self.sandboxes.request,
            "sandbox.options": self.sandboxes.options,
            "sandbox.get": self.sandboxes.get,
            "sandbox.sync": self.sandboxes.sync,
            "sandbox.list": self.sandboxes.list_sandboxes,
            "sandbox.release": self.sandboxes.release,
            "sandbox.terminal": self.sandboxes.terminal,
            "sandbox.health": self.sandboxes.health,
            "feed.register": self.feed.register,
            "feed.post": self.feed.post,
            "feed.list": self.feed.list_posts,
        }
        self.tools = ToolDispatcher(
            handlers=handlers,
            permissions=self.permissions,
            activity=self.activity,
            tool_calls=self.tool_calls,
        )

    def reflection_create(
        self,
        *,
        project_id: str,
        title: str = "",
        lenses: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._external_reflection_state(
            self.syntheses.create(project_id=project_id, title=title, lenses=lenses or [])
        )

    def reflection_get(
        self, *, project_id: str, reflection_id: str
    ) -> dict[str, Any]:
        return self._external_reflection_state(
            self.syntheses.get_state(synthesis_id=reflection_id, project_id=project_id)
        )

    def reflection_list(self, *, project_id: str) -> dict[str, Any]:
        state = self.syntheses.list_syntheses(project_id=project_id)
        return {
            "count": state.get("count", len(state.get("syntheses", []))),
            "reflections": [
                self._external_reflection_state(item)
                for item in state.get("syntheses", [])
            ],
        }

    def reflection_transition(
        self, *, project_id: str, reflection_id: str, transition: str
    ) -> dict[str, Any]:
        internal_transition = (
            "submit_synthesis"
            if transition == "submit_reflection_artifacts"
            else transition
        )
        return self._external_reflection_state(
            self.syntheses.transition(
                project_id=project_id,
                synthesis_id=reflection_id,
                transition=internal_transition,
            )
        )

    def _external_reflection_state(self, state: dict[str, Any]) -> dict[str, Any]:
        output = dict(state)
        if output.get("status") == "synthesis_review":
            output["status"] = "reflection_review"
        if "allowed_transitions" in output:
            output["allowed_transitions"] = [
                self._external_reflection_transition(item)
                for item in output.get("allowed_transitions", [])
            ]
        return output

    def _external_reflection_transition(self, item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        output = dict(item)
        if output.get("transition") == "submit_synthesis":
            output["transition"] = "submit_reflection_artifacts"
        if output.get("leads_to") == "synthesis_review":
            output["leads_to"] = "reflection_review"
        text_fields = ("requires", "description")
        for field in text_fields:
            if isinstance(output.get(field), str):
                output[field] = output[field].replace(
                    "synthesis_reviewer",
                    "reflection_reviewer",
                ).replace(
                    "submit_synthesis",
                    "submit_reflection_artifacts",
                )
        return output

    def current_project(self, *, tenant_id: str | None = None) -> dict[str, Any]:
        return self.project_overview.current_project(tenant_id=tenant_id)

    def list_tools(self) -> list[dict[str, Any]]:
        return self.tools.list_tools()

    def shutdown(self) -> None:
        """Best-effort: stop background provisioning jobs and the sync poller."""
        try:
            self.sandboxes.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.execution_backend.shutdown()
        except Exception:  # noqa: BLE001
            pass

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        activity_source: str = "app",
        internal_kwargs: dict[str, Any] | None = None,
        telemetry_project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.tools.call_tool(
            name=name,
            arguments=arguments,
            activity_source=activity_source,
            internal_kwargs=internal_kwargs,
            telemetry_project_id=telemetry_project_id,
        )

    def _activity_hook(self, event_type: str, payload: dict[str, Any]) -> None:
        """Bridge backend emit-style logging and ActivityLogger."""
        try:
            self.activity.emit(event_type=event_type, payload=payload)
        except Exception:  # noqa: BLE001
            pass

"""Unified brain app without checkout-local workspace/runtime wiring."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from merv.shared.storage_guidance import STORAGE_RULE_OF_THUMB, storage_guidance

from ...application.events import EventDispatcher
from ...application.experiments.context import ExperimentContextQuery
from ...application.experiments.create import CreateExperiment
from ...application.experiments.exhibits import ExperimentExhibits
from ...application.experiments.queries import ExperimentCollectionQuery
from ...application.experiments.reactions import ExperimentReactions
from ...application.experiments.tracking import (
    AgentExperimentQuery,
    ExperimentDetailQuery,
    FinalizeTrackingRun,
    GetTrackingContext,
)
from ...application.experiments.transition import TransitionExperiment
from ...application.queries import ComputeCostQuery, ExperimentFigureQuery, LogicGraphQuery, MlflowOverviewQuery, TenantCountersQuery
from ...application.project_context import ProjectContextQuery
from ...application.timeline import EventTimelineQuery
from ...application.reflections import ReflectionCommands
from ...application.status_guidance import StatusGuidancePolicy
from ...application.workflow import ProjectDashboardQuery, StatusAndNextQuery
from ...application.reviews import ReadReviewStatus, StartReviewSession
from ...application.tool_commands import ControlToolOperations
from ...research_core.facade import ResearchCoreFacade
from ...research_core.project_context import ProjectContextFactsReader
from ...research_core.snapshots import ResearchSnapshotReader
from ..tools.contracts import available_tool_names
from .control_runtime import ControlActivitySink, ControlToolCallSink
from ..observability import StructuredLogger
from ..user_settings import UserHfTokenSettings
from ...kernel.ports.mgmt_keys import MgmtKeyStore
from ...kernel.ports.blob_store import EvidenceBlobStore
from .record_core import build_record_core
from ...sandbox.sandbox_backend import SandboxBackend
from ...sandbox.sandbox_support import ACTIVE_SANDBOX_STATUSES
from ...sandbox.facade import SandboxFacade
from ...sandbox.runtime import build_sandbox_runtime
from ...object_storage.service import StorageLedgerService
from ...object_storage.catalog import StorageObjectCatalog
from ...kernel.state import BaseStateStore
from ...kernel.state.tool_call_ledger import ToolCallLedger
from ..artifacts import ArtifactTools
from ..tools.tool_facade import ToolDispatcher
from ..tools.tool_handlers import build_control_tool_handlers
from ..transport.api.dependencies import HttpDependencies


class ControlApp:
    """Brain app: record services, policy, and sandbox lifecycle; no checkout I/O."""

    def __init__(
        self,
        *,
        store: BaseStateStore,
        blobs: EvidenceBlobStore,
        storage: StorageLedgerService | None,
        execution_backend: SandboxBackend,
        mgmt_keys: MgmtKeyStore,
        mlflow_tracking: Any | None = None,
        force_expiry_reaper: bool = False,
        structured_logging: bool = False,
    ) -> None:
        self._store = store
        self.activity = ControlActivitySink()
        self.tool_calls = ControlToolCallSink()
        # Durable sibling of the ring above: the rings still serve the debug
        # UI's raw payload view; the ledger keeps sizes and outcomes past a
        # restart. A dropped row announces itself through the activity feed.
        self.tool_ledger = ToolCallLedger(store=store, on_failure=self._ledger_dropped)
        self.structured_logger = StructuredLogger(enabled=structured_logging)
        self._blobs = blobs
        self._storage = storage
        self._execution_backend = execution_backend
        # The legacy tracking adapter remains injectable for compatibility and
        # a later reintroduction, but it is no longer auto-composed from the
        # environment.  A normal product build therefore has no tracking
        # service, tools, routes, credentials, or response fields.
        self._tracking = mlflow_tracking
        core = self._record_core = build_record_core(store=store, blobs=blobs)

        self.research_core = ResearchCoreFacade(
            core.experiments,
            reflections=core.reflection_waves,
            graph_refs=core.graph_refs,
        )
        self.reflection_commands = ReflectionCommands(reflections=self.research_core)
        self.produced_objects = StorageObjectCatalog(store=store)
        self.artifacts = core.artifacts
        self.artifact_tools = ArtifactTools(artifacts=self.artifacts)
        self.project_context = ProjectContextQuery(
            facts=ProjectContextFactsReader(store=store),
            artifacts=self.artifacts,
        )
        self.experiment_context = ExperimentContextQuery(artifacts=self.artifacts)
        self.experiment_exhibits = ExperimentExhibits(
            research=self.research_core,
            artifacts=self.artifacts,
            tracking=self._tracking,
        )
        self.reaction_registry = EventDispatcher()
        self.experiment_reactions = ExperimentReactions(
            research=self.research_core,
            feed=core.feed,
            tracking=self._tracking,
        )
        self.experiment_reactions.bind(self.reaction_registry)
        self.transition_experiment = TransitionExperiment(
            research=self.research_core,
            artifacts=self.artifacts,
            tracking=self._tracking,
            exhibits=self.experiment_exhibits,
            dispatcher=self.reaction_registry,
            objects=self.produced_objects,
        )
        self.read_review_status = ReadReviewStatus(
            research=self.research_core,
            reviews=core.reviews,
            dispatcher=self.reaction_registry,
        )
        self.tracking_context = GetTrackingContext(
            research=self.research_core, tracking=self._tracking
        )
        self.agent_experiment_query = AgentExperimentQuery(
            research=self.research_core,
            objects=self.produced_objects,
            tracking=self._tracking,
        )
        self.start_review_session = StartReviewSession(
            reviews=core.reviews,
            research=self.research_core,
            experiment_context=self.experiment_context,
            project_context=self.project_context,
            reflections=self.reflection_commands,
        )
        self.experiment_detail_query = ExperimentDetailQuery(
            research=self.research_core,
            objects=self.produced_objects,
            tracking=self._tracking,
        )
        self.finalize_tracking_run = FinalizeTrackingRun(
            research=self.research_core,
            feed=core.feed,
            tracking=self._tracking,
            dispatcher=self.reaction_registry,
            objects=self.produced_objects,
        )
        self.experiment_collection_query = ExperimentCollectionQuery(
            research=self.research_core,
            objects=self.produced_objects,
        )
        self.create_experiment = CreateExperiment(research=self.research_core)
        self.control_tool_operations = ControlToolOperations(
            projects=core.projects,
            experiments=self.experiment_collection_query,
            project_context=self.project_context,
            storage=storage,
        )

        self._sandbox_runtime = build_sandbox_runtime(
            store=store,
            backend=execution_backend,
            mgmt_keys=mgmt_keys,
            force_expiry_reaper=force_expiry_reaper,
        )
        self.sandboxes = SandboxFacade(
            runtime=self._sandbox_runtime,
            quotas=core.quotas,
            storage_enabled=storage is not None,
            # The sandbox module embeds/calls the component-owned values it is handed.
            storage_hint=STORAGE_RULE_OF_THUMB,
            attachment_check=self.research_core.assert_experiment_in_project,
        )
        # Retention has to be enforced IN PROCESS: the hosted runtime schedules
        # no cleanup pass, so a 30-day horizon that waits on an external cron is
        # a horizon nothing enforces. The reaper is the only timer this process
        # owns, and the prune it now carries is bounded and batched.
        self._sandbox_runtime.daemons.periodic_maintenance = self.tool_ledger.prune
        self._sandbox_runtime.start()
        self.research_snapshots = ResearchSnapshotReader(
            store=store,
            experiments=core.experiments,
            reflections=core.reflection_waves,
        )
        self.next_action_policy = StatusGuidancePolicy(
            storage_enabled=storage is not None,
            storage_guidance=storage_guidance(enabled=storage is not None),
        )
        self.workflow = StatusAndNextQuery(
            snapshots=self.research_snapshots,
            sandboxes=self.sandboxes,
            policy=self.next_action_policy,
            objects=self.produced_objects,
            context=self.experiment_context,
            project_context=self.project_context,
        )
        self.event_timeline = EventTimelineQuery(source=store)
        self.project_dashboard_query = ProjectDashboardQuery(
            snapshots=self.research_snapshots,
            workflow=self.workflow,
            artifacts=self.artifacts,
            review_queue=core.reviews.queue,
            recent_events=self.event_timeline.recent,
            health=(lambda: self._tracking.health()) if self._tracking else (lambda: {}),
            current=core.projects.current,
        )
        self.mlflow_overview_query = (
            MlflowOverviewQuery(
                experiments=self.research_core.project_experiment_summaries,
                tracking=self._tracking,
            )
            if self._tracking is not None
            else None
        )
        self.experiment_figure_query = ExperimentFigureQuery(
            experiment_state=self.research_core.experiment_state,
            review_snapshot=core.reviews.snapshot_from_id,
            open_reviews=core.reviews.open_requests_for_target,
            sandbox_row=self.sandboxes.get_row,
            sandbox_view=self.sandboxes.row_view,
            sandbox_status_active=ACTIVE_SANDBOX_STATUSES.__contains__,
        )
        self.compute_cost_query = ComputeCostQuery(
            project_spend=core.quotas.project_spend,
            experiments=self.research_core.project_experiment_summaries,
        )
        self.tenant_counters_query = TenantCountersQuery(
            event_count=store.tenant_event_count,
            generation_counters=core.quotas.tenant_generation_counters,
        )
        self.logic_graph_query = LogicGraphQuery(
            research=self.research_core,
            artifacts=self.artifacts,
        )
        self.user_settings = UserHfTokenSettings(store=store)
        tool_names = available_tool_names(
            storage_enabled=storage is not None,
            tracking_enabled=self._tracking is not None,
        )
        self.tools = ToolDispatcher(
            handlers=build_control_tool_handlers(
                workflow=self.workflow,
                projects=core.projects,
                claims=core.claims,
                create_experiment=self.create_experiment,
                reflection_tools=self.reflection_commands,
                artifact_submissions=self.artifact_tools,
                storage=storage,
                reviews=core.reviews,
                review_session=self.start_review_session,
                sandboxes=self.sandboxes,
                feed=core.feed,
                experiment_transition=self.transition_experiment,
                experiment_exhibit=self.experiment_exhibits,
                tracking_context=self.tracking_context,
                agent_experiment=self.agent_experiment_query,
                tracking_finalize=self.finalize_tracking_run,
                review_status=self.read_review_status,
                operations=self.control_tool_operations,
                litreview=core.literature,
                tracking_enabled=self._tracking is not None,
            ),
            permissions=core.permissions,
            activity=self.activity,
            tool_calls=self.tool_calls,
            ledger=self.tool_ledger,
            tool_names=tool_names,
        )
        self.http = HttpDependencies(
            projects=core.projects,
            reviews=core.reviews,
            artifacts=core.artifacts,
            feed=core.feed,
            sandboxes=self.sandboxes,
            storage=storage,
            timeline=self.event_timeline,
            activity=self.activity,
            tool_calls=self.tool_calls,
            tool_ledger=self.tool_ledger,
            tools=self.tools,
            structured_log=self.structured_logger,
            experiment_detail=self.experiment_detail_query,
            experiment_collection=self.experiment_collection_query,
            compute_cost=self.compute_cost_query,
            logic_graph=self.logic_graph_query,
            workflow=self.workflow,
            dashboard=self.project_dashboard_query,
            experiment_figure=self.experiment_figure_query,
            tracking_overview=self.mlflow_overview_query,
            tenant_counters=self.tenant_counters_query,
            literature=core.literature,
            user_settings=self.user_settings,
        )

    def _ledger_dropped(self, error: str) -> None:
        """A ledger row that could not be written is itself telemetry, and says
        so in the activity feed rather than vanishing."""
        with suppress(Exception):
            self.activity.emit(
                event_type="telemetry.dropped",
                payload={"sink": "tool_calls", "status": "error", "error": error},
            )

    def shutdown(self) -> None:
        """Stop what still calls the subsystems, then close the subsystems.

        The order is load-bearing, not alphabetical: the reaper thread is what
        drives the ledger's retention sweep (``periodic_maintenance`` above), so
        it is signalled and joined FIRST. Closing the ledger under a live sweep
        would be closing a database connection out from under a running
        statement. ``ToolCallLedger.close()`` defends itself as well — it takes
        the writer lock, and declines to close a handle whose owner may still be
        mid-row — but a shutdown that raced its own daemons would be leaning on
        that defense every time rather than in the case it exists for.
        """
        with suppress(Exception):
            self._sandbox_runtime.shutdown()  # signals + joins the reaper
        with suppress(Exception):
            self._execution_backend.shutdown()
        with suppress(Exception):  # the ledger holds one connection per writer
            self.tool_ledger.close()

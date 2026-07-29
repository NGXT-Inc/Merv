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
from ...application.reviews import (
    ReadReviewStatus,
    RequestReview,
    ReviewQueue,
    StartReviewSession,
)
from ...application.tool_commands import ControlToolOperations
from ...artifacts import Artifacts
from ...feed import FeedService
from ...literature import Literature
from ...research_core import Research, ResearchTargets
from ..tools.contracts import available_tool_names
from .control_runtime import ControlActivitySink, ControlToolCallSink
from ..observability import StructuredLogger
from ..user_settings import UserHfTokenSettings
from ...kernel.ports.mgmt_keys import MgmtKeyStore
from ...kernel.ports.blob_store import EvidenceBlobStore
from ...object_storage import ObjectStorage
from ...sandbox import SandboxBackend, SandboxEngine
from ...kernel.state import BaseStateStore
from ...kernel.state.tool_call_ledger import ToolCallLedger
from ..artifacts import ArtifactTools
from ..permissions import PermissionService
from ..tools.tool_facade import ToolDispatcher
from ..tools.tool_handlers import build_control_tool_handlers
from ..transport.api.dependencies import HttpDependencies
from ..web_preview import AllowlistedPaperPreview, NetworkWebPreview


class ControlApp:
    """Brain app: record services, policy, and sandbox lifecycle; no checkout I/O."""

    def __init__(
        self,
        *,
        store: BaseStateStore,
        blobs: EvidenceBlobStore,
        storage: ObjectStorage,
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
        self.tool_ledger.start_retention()
        self.structured_logger = StructuredLogger(enabled=structured_logging)
        self._blobs = blobs
        self.object_storage = storage
        self._storage = storage if storage.enabled else None
        # The legacy tracking adapter remains injectable for compatibility and
        # a later reintroduction, but it is no longer auto-composed from the
        # environment.  A normal product build therefore has no tracking
        # service, tools, routes, credentials, or response fields.
        self._tracking = mlflow_tracking
        self.artifacts = Artifacts(
            store=store,
            blobs=blobs,
            targets=ResearchTargets(),
        )
        self.research = Research(store=store, artifacts=self.artifacts)
        self.feed = FeedService(
            store=store,
            blobs=blobs,
            web_preview=NetworkWebPreview(),
        )
        self.literature = Literature(
            store=store, unfurl=AllowlistedPaperPreview()
        )
        self.reflection_commands = ReflectionCommands(
            reflections=self.research
        )
        self.produced_objects = storage
        self.artifact_tools = ArtifactTools(artifacts=self.artifacts)
        self.project_context = ProjectContextQuery(
            research=self.research,
            artifacts=self.artifacts,
        )
        self.experiment_context = ExperimentContextQuery(artifacts=self.artifacts)
        self.experiment_exhibits = ExperimentExhibits(
            research=self.research,
            artifacts=self.artifacts,
            tracking=self._tracking,
        )
        self.reaction_registry = EventDispatcher()
        self.experiment_reactions = ExperimentReactions(
            research=self.research,
            feed=self.feed,
            tracking=self._tracking,
        )
        self.experiment_reactions.bind(self.reaction_registry)
        self.transition_experiment = TransitionExperiment(
            research=self.research,
            artifacts=self.artifacts,
            tracking=self._tracking,
            exhibits=self.experiment_exhibits,
            dispatcher=self.reaction_registry,
            objects=self.produced_objects,
        )
        self.read_review_status = ReadReviewStatus(
            research=self.research,
            dispatcher=self.reaction_registry,
        )
        self.tracking_context = GetTrackingContext(
            research=self.research, tracking=self._tracking
        )
        self.agent_experiment_query = AgentExperimentQuery(
            research=self.research,
            objects=self.produced_objects,
            tracking=self._tracking,
        )
        self.start_review_session = StartReviewSession(
            research=self.research,
            artifacts=self.artifacts,
            experiment_context=self.experiment_context,
            project_context=self.project_context,
            reflections=self.reflection_commands,
        )
        self.request_review = RequestReview(research=self.research)
        self.review_queue = ReviewQueue(research=self.research)
        self.experiment_detail_query = ExperimentDetailQuery(
            research=self.research,
            objects=self.produced_objects,
            tracking=self._tracking,
        )
        self.finalize_tracking_run = FinalizeTrackingRun(
            research=self.research,
            feed=self.feed,
            tracking=self._tracking,
            dispatcher=self.reaction_registry,
            objects=self.produced_objects,
        )
        self.experiment_collection_query = ExperimentCollectionQuery(
            research=self.research,
            objects=self.produced_objects,
        )
        self.create_experiment = CreateExperiment(research=self.research)
        self.control_tool_operations = ControlToolOperations(
            research=self.research,
            experiments=self.experiment_collection_query,
            project_context=self.project_context,
        )

        self.sandboxes = SandboxEngine(
            store=store,
            backend=execution_backend,
            mgmt_keys=mgmt_keys,
            force_expiry_reaper=force_expiry_reaper,
            storage_enabled=storage.enabled,
            # The sandbox module embeds/calls the component-owned values it is handed.
            storage_hint=STORAGE_RULE_OF_THUMB,
            attachment_check=self.research.assert_experiment_in_project,
        )
        self.sandboxes.start()
        self.next_action_policy = StatusGuidancePolicy(
            storage_enabled=storage.enabled,
            storage_guidance=storage_guidance(enabled=storage.enabled),
        )
        self.workflow = StatusAndNextQuery(
            research=self.research,
            sandboxes=self.sandboxes,
            policy=self.next_action_policy,
            objects=self.produced_objects,
            context=self.experiment_context,
            project_context=self.project_context,
        )
        self.event_timeline = EventTimelineQuery(source=store)
        self.project_dashboard_query = ProjectDashboardQuery(
            research=self.research,
            workflow=self.workflow,
            artifacts=self.artifacts,
            review_queue=self.review_queue,
            recent_events=self.event_timeline.recent,
            health=(lambda: self._tracking.health()) if self._tracking else (lambda: {}),
            current=self.research.current_project,
        )
        self.mlflow_overview_query = (
            MlflowOverviewQuery(
                experiments=self.research.project_experiment_summaries,
                tracking=self._tracking,
            )
            if self._tracking is not None
            else None
        )
        self.experiment_figure_query = ExperimentFigureQuery(
            experiment_state=self.research.experiment_state,
            review_snapshot=self.research.review_snapshot,
            open_reviews=self.research.open_experiment_reviews,
            sandbox_snapshot=self.sandboxes.figure_snapshot,
        )
        self.compute_cost_query = ComputeCostQuery(
            project_spend=self.sandboxes.project_spend,
            experiments=self.research.project_experiment_summaries,
        )
        self.tenant_counters_query = TenantCountersQuery(
            event_count=store.tenant_event_count,
            generation_counters=self.sandboxes.tenant_generation_counters,
        )
        self.logic_graph_query = LogicGraphQuery(
            research=self.research,
            artifacts=self.artifacts,
        )
        self.user_settings = UserHfTokenSettings(store=store)
        tool_names = available_tool_names(
            storage_enabled=storage.enabled,
            tracking_enabled=self._tracking is not None,
        )
        self.tools = ToolDispatcher(
            handlers=build_control_tool_handlers(
                workflow=self.workflow,
                research=self.research,
                create_experiment=self.create_experiment,
                reflection_tools=self.reflection_commands,
                artifact_submissions=self.artifact_tools,
                storage=self._storage,
                review_request=self.request_review,
                review_session=self.start_review_session,
                sandboxes=self.sandboxes,
                feed=self.feed,
                experiment_transition=self.transition_experiment,
                experiment_exhibit=self.experiment_exhibits,
                tracking_context=self.tracking_context,
                agent_experiment=self.agent_experiment_query,
                tracking_finalize=self.finalize_tracking_run,
                review_status=self.read_review_status,
                operations=self.control_tool_operations,
                litreview=self.literature,
                tracking_enabled=self._tracking is not None,
            ),
            permissions=PermissionService(),
            activity=self.activity,
            tool_calls=self.tool_calls,
            ledger=self.tool_ledger,
            tool_names=tool_names,
        )
        self.http = HttpDependencies(
            research=self.research,
            artifacts=self.artifacts,
            feed=self.feed,
            sandboxes=self.sandboxes,
            storage=self._storage,
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
            review_queue=self.review_queue,
            literature=self.literature,
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

        Sandbox stops its timer, provision jobs, and provider resources. The
        ledger then stops its own retention timer before closing cached writers.
        """
        with suppress(Exception):
            self.sandboxes.shutdown()  # signals + joins the reaper
        with suppress(Exception):  # the ledger holds one connection per writer
            self.tool_ledger.close()

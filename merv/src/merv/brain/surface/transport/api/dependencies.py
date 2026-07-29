"""Typed public capabilities composed for the HTTP delivery adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Request

from ....application.facade import (
    ComputeCostQuery,
    EventTimelineQuery,
    ExperimentCollectionQuery,
    ExperimentDetailQuery,
    ExperimentFigureQuery,
    LogicGraphQuery,
    MlflowOverviewQuery,
    ProjectDashboardQuery,
    StatusAndNextQuery,
    TenantCountersQuery,
)
from ....application.ports.storage import ObjectStorage
from ....artifacts import Artifacts
from ...user_settings import UserHfTokenSettings
from ....feed import FeedService
from ....research_core.facade import (
    ResearchLiterature,
    ResearchProjects,
    ResearchReviewDelivery,
)
from ....sandbox import SandboxEngine
from ...observability import StructuredLogger
from ...tools.tool_facade import ToolDispatcher


class ActivityTelemetry(Protocol):
    def recent(self, **kwargs: Any) -> dict[str, Any]: ...
    def emit(self, *, event_type: str, payload: dict[str, Any]) -> None: ...


class ToolCallTelemetry(Protocol):
    def stats(self, **kwargs: Any) -> dict[str, Any]: ...
    def get(self, **kwargs: Any) -> dict[str, Any] | None: ...
    def clear(self, **kwargs: Any) -> dict[str, Any]: ...


class ToolCallLedgerWrites(Protocol):
    """Durable ledger writes: one row per call, one per pre-dispatch refusal."""

    def record(self, **kwargs: Any) -> None: ...
    def reject(self, **kwargs: Any) -> None: ...


class AuthorizeProject(Protocol):
    def __call__(self, request: Request, project_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class HttpDependencies:
    """Bootstrap-built public contracts; routers receive only their own fields."""

    projects: ResearchProjects
    reviews: ResearchReviewDelivery
    artifacts: Artifacts
    feed: FeedService
    sandboxes: SandboxEngine
    storage: ObjectStorage | None
    timeline: EventTimelineQuery
    activity: ActivityTelemetry
    tool_calls: ToolCallTelemetry
    tool_ledger: ToolCallLedgerWrites
    tools: ToolDispatcher
    structured_log: StructuredLogger
    experiment_detail: ExperimentDetailQuery
    experiment_collection: ExperimentCollectionQuery
    compute_cost: ComputeCostQuery
    logic_graph: LogicGraphQuery
    workflow: StatusAndNextQuery
    dashboard: ProjectDashboardQuery
    experiment_figure: ExperimentFigureQuery
    tracking_overview: MlflowOverviewQuery | None
    tenant_counters: TenantCountersQuery
    literature: ResearchLiterature
    user_settings: UserHfTokenSettings

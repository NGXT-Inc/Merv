"""Application-owned contract for experiment tracking.

These types describe only the tracking behavior used by experiment workflows.
They are internal structural contracts, not new public wire models.  Concrete
tracking products live outside this package and implement ``ExperimentTracking``.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping, Protocol, TypedDict, runtime_checkable


# The application deliberately bounds a metrics exhibit rather than treating
# an external tracking service as an unlimited archive mirror.
MAX_TRACKING_SNAPSHOT_RUNS: Final = 50


@dataclass(frozen=True, slots=True)
class TrackingCapabilities:
    """Independent configuration facts exposed by a tracking adapter.

    ``logging`` means an execution agent can log to the advertised endpoint;
    ``control`` means the backend can create or update runs; and ``readback``
    means the backend can query runs for exhibits.  Keeping these separate is
    important because a backend-only endpoint supports control and readback
    without being reachable from an execution environment.
    """

    logging: bool
    control: bool
    readback: bool


TRACKING_CAPABILITY_TRUTH_TABLE: Final[
    Mapping[tuple[bool, bool], TrackingCapabilities]
] = MappingProxyType(
    {
        (False, False): TrackingCapabilities(
            logging=False, control=False, readback=False
        ),
        (True, False): TrackingCapabilities(
            logging=True, control=False, readback=True
        ),
        (False, True): TrackingCapabilities(
            logging=False, control=True, readback=True
        ),
        (True, True): TrackingCapabilities(
            logging=True, control=True, readback=True
        ),
    }
)


def capabilities_for_configuration(
    *, logging: bool, control: bool
) -> TrackingCapabilities:
    """Resolve the explicit logging/control configuration truth table."""
    return TRACKING_CAPABILITY_TRUTH_TABLE[(bool(logging), bool(control))]


class TrackingContextPayload(TypedDict, total=False):
    configured: bool
    mode: str
    tracking_uri: str
    dashboard_url: str
    experiment_name: str
    env: dict[str, str]
    note: str


@runtime_checkable
class TrackingContext(Protocol):
    @property
    def configured(self) -> bool: ...

    @property
    def experiment_name(self) -> str: ...

    def to_dict(self) -> TrackingContextPayload: ...


class TrackingRun(TypedDict, total=False):
    run_id: str
    run_name: str
    status: str
    artifact_uri: str
    created_at: str
    ended_at: str
    created_by_plugin: bool
    experiment_id: str
    dashboard_run_url: str
    error: str


class CreateRunResult(TypedDict, total=False):
    created: bool
    configured: bool
    control_configured: bool
    experiment_name: str
    experiment_id: str
    run_id: str
    run_name: str
    status: str
    artifact_uri: str
    created_at: str
    dashboard_run_url: str
    error: str
    note: str


class TrackingRunUpdate(TypedDict, total=False):
    attempted: bool
    status: str | None
    applied: bool
    skipped_already_terminal: str
    error: str
    note: str


class FinalizeRunResult(TypedDict, total=False):
    configured: bool
    control_configured: bool
    experiment_name: str
    run_id: str
    requested_status: str | None
    update: TrackingRunUpdate
    readback_attempts: int
    terminal: bool
    run: TrackingRun
    error: str
    note: str


class TrackingMetric(TypedDict, total=False):
    last: float | None
    step: object
    timestamp: object
    min: float
    max: float


class TrackingSnapshotRun(TypedDict, total=False):
    run_id: str
    run_name: str
    status: str
    start_time: int
    end_time: int
    params: dict[str, object]
    tags: dict[str, str]
    metrics: dict[str, TrackingMetric]
    history: dict[str, list[list[object]]]
    metrics_capped_at: int


class TrackingExperimentSnapshot(TypedDict, total=False):
    experiment_id: str
    name: str
    last_update_time: object
    runs: list[TrackingSnapshotRun]


class MetricsSnapshot(TypedDict, total=False):
    available: bool
    source: str
    experiment_id: str
    experiments: list[TrackingExperimentSnapshot]
    dashboard_experiment_url: str
    hint: str


@runtime_checkable
class ExperimentTracking(Protocol):
    """Small command/readback port needed by experiment workflows."""

    def capabilities(self) -> TrackingCapabilities: ...

    def context(
        self,
        *,
        project_id: str,
        experiment_id: str,
        include_credentials: bool = False,
    ) -> TrackingContext: ...

    def create_run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        attempt_index: int,
        run_name: str,
    ) -> CreateRunResult: ...

    def finalize_run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        run_id: str,
        status: str,
        wait_seconds: float,
    ) -> FinalizeRunResult: ...

    def results_metrics(
        self, *, project_id: str, experiment_id: str
    ) -> MetricsSnapshot: ...


__all__ = [
    "CreateRunResult",
    "ExperimentTracking",
    "FinalizeRunResult",
    "MAX_TRACKING_SNAPSHOT_RUNS",
    "MetricsSnapshot",
    "TRACKING_CAPABILITY_TRUTH_TABLE",
    "TrackingCapabilities",
    "TrackingContext",
    "TrackingContextPayload",
    "TrackingExperimentSnapshot",
    "TrackingMetric",
    "TrackingRun",
    "TrackingRunUpdate",
    "TrackingSnapshotRun",
    "capabilities_for_configuration",
]

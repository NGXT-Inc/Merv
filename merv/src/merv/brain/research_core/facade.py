"""Stable Research entrypoint for cross-component experiment workflows."""

from __future__ import annotations

from typing import Protocol, TypedDict, cast, runtime_checkable

from .domain.paths import experiment_folder_rel
from .experiment_views import slim_experiment_state
from .experiments import ExperimentService
from .transition_types import CommittedExperimentTransition


class PersistedRunState(TypedDict, total=False):
    run_id: str | None
    run_name: str
    status: str
    artifact_uri: str
    created_at: str | None
    created_by_plugin: bool
    error: str


class ExperimentState(TypedDict, total=False):
    id: str
    project_id: str
    name: str
    status: str
    attempt_index: int
    mlflow_run: PersistedRunState | None


class ExhibitVerdict(TypedDict, total=False):
    runs_found: int
    result_files: int
    attempt_index: int
    mlflow: dict[str, object]
    pinned: bool


class SlimExperimentState(TypedDict, total=False):
    id: str
    project_id: str
    name: str
    status: str
    attempt_index: int
    mlflow_run: PersistedRunState | None


@runtime_checkable
class ResearchCore(Protocol):
    def experiment_state(
        self, *, experiment_id: str, project_id: str | None = None
    ) -> ExperimentState: ...

    def transition_experiment(
        self,
        *,
        experiment_id: str,
        transition: str,
        evidence: dict[str, object] | None = None,
        project_id: str | None = None,
    ) -> CommittedExperimentTransition: ...

    def record_tracking_run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        run: PersistedRunState,
        event_type: str | None = None,
    ) -> ExperimentState: ...

    def record_exhibit_verdict(
        self,
        *,
        experiment_id: str,
        project_id: str,
        verdict: ExhibitVerdict,
    ) -> None: ...

    def attempt_started_running_at(self, *, experiment_id: str) -> str | None: ...

    def exhibit_path(self, *, experiment_id: str, name: str, filename: str) -> str: ...

    def present_experiment(self, state: ExperimentState) -> SlimExperimentState: ...


class ResearchCoreFacade:
    """Narrow adapter over the already-composed experiment service."""

    __slots__ = ("_experiments",)

    def __init__(self, experiments: ExperimentService) -> None:
        self._experiments = experiments

    def experiment_state(
        self, *, experiment_id: str, project_id: str | None = None
    ) -> ExperimentState:
        return cast(
            ExperimentState,
            self._experiments.get_state(
                experiment_id=experiment_id, project_id=project_id
            ),
        )

    def transition_experiment(
        self,
        *,
        experiment_id: str,
        transition: str,
        evidence: dict[str, object] | None = None,
        project_id: str | None = None,
    ) -> CommittedExperimentTransition:
        return self._experiments.transition_with_event(
            experiment_id=experiment_id,
            transition=transition,
            evidence=evidence,
            project_id=project_id,
        )

    def record_tracking_run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        run: PersistedRunState,
        event_type: str | None = None,
    ) -> ExperimentState:
        return cast(
            ExperimentState,
            self._experiments.record_mlflow_run(
                project_id=project_id,
                experiment_id=experiment_id,
                run=run,
                event_type=event_type,
            ),
        )

    def record_exhibit_verdict(
        self,
        *,
        experiment_id: str,
        project_id: str,
        verdict: ExhibitVerdict,
    ) -> None:
        self._experiments.record_exhibit_verdict(
            experiment_id=experiment_id,
            project_id=project_id,
            verdict=verdict,
        )

    def attempt_started_running_at(self, *, experiment_id: str) -> str | None:
        return self._experiments.attempt_started_running_at(
            experiment_id=experiment_id
        )

    def exhibit_path(self, *, experiment_id: str, name: str, filename: str) -> str:
        return f"{experiment_folder_rel(experiment_id=experiment_id, name=name)}{filename}"

    def present_experiment(self, state: ExperimentState) -> SlimExperimentState:
        return cast(SlimExperimentState, slim_experiment_state(state))


__all__ = [
    "CommittedExperimentTransition",
    "ExhibitVerdict",
    "ExperimentState",
    "PersistedRunState",
    "ResearchCore",
    "ResearchCoreFacade",
    "SlimExperimentState",
]

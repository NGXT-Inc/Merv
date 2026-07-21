"""Application read model for experiment metrics exhibits."""

from __future__ import annotations

from typing import Protocol

from ...artifacts.facade import Artifacts
from ...kernel.utils import WorkflowError
from ...research_core.facade import ExperimentState, ResearchCore
from ..ports.tracking import ExperimentTracking, tracking_experiment_name
from .metrics_exhibit import METRICS_EXHIBIT_FILENAME, build_metrics_exhibit


class ExhibitBuilder(Protocol):
    def generate(self, *, state: ExperimentState) -> dict[str, object]: ...


class ExperimentExhibits:
    """Build current observations; transition decides whether to commit them."""

    def __init__(
        self,
        *,
        research: ResearchCore,
        artifacts: Artifacts,
        tracking: ExperimentTracking | None,
    ) -> None:
        self.research = research
        self.artifacts = artifacts
        self.tracking = tracking

    def generate(self, *, state: ExperimentState) -> dict[str, object]:
        project_id = str(state.get("project_id") or "")
        experiment_id = str(state.get("id") or "")
        attempt_index = int(state.get("attempt_index") or 1)
        capabilities = self.tracking.capabilities() if self.tracking else None
        configured = bool(capabilities and capabilities.readback)
        snapshot = (
            self.tracking.results_metrics(
                project_id=project_id, experiment_id=experiment_id
            )
            if self.tracking and configured
            else None
        )
        return build_metrics_exhibit(
            project_id=project_id,
            experiment_id=experiment_id,
            attempt_index=attempt_index,
            experiment_name=tracking_experiment_name(
                project_id=project_id, experiment_id=experiment_id
            ),
            window_started_at=self.research.attempt_started_running_at(
                experiment_id=experiment_id
            ),
            snapshot=snapshot,
            mlflow_configured=configured,
            file_sources=self.artifacts.metric_file_sources(
                experiment_id=experiment_id, attempt_index=attempt_index
            ),
        )

    def preview(
        self, *, experiment_id: str, project_id: str | None = None
    ) -> dict[str, object]:
        state = self.research.experiment_state(
            experiment_id=experiment_id, project_id=project_id
        )
        if str(state.get("status")) != "running":
            raise WorkflowError(
                "experiment.exhibit previews a running experiment; this one is "
                f"{state.get('status')!r}. After submit_results, read the pinned "
                "exhibit resource instead (resource.find)."
            )
        exhibit = self.generate(state=state)
        path = self.research.exhibit_path(
            experiment_id=str(state.get("id") or experiment_id),
            name=str(state.get("name") or ""),
            filename=METRICS_EXHIBIT_FILENAME,
        )
        return {
            "project_id": str(state.get("project_id") or ""),
            "experiment_id": experiment_id,
            "exhibit_path": path,
            "exhibit": exhibit,
            "guidance": (
                "Preview of the system-generated metrics exhibit. At "
                "submit_results the system regenerates it from the same sources "
                f"and pins it at {path} when matching runs are found, or when "
                "MLflow is unavailable after a plugin-created run. The "
                "newest 50 runs are captured without curation and the exhibit "
                "records when that cap is reached. Later runs remain in MLflow "
                "but are outside the finalized exhibit. When pinned, report.md "
                f"must reference {METRICS_EXHIBIT_FILENAME} and interpret it "
                "rather than restate numbers by hand."
            ),
        }


def should_pin_exhibit(
    *, exhibit: dict[str, object], state: ExperimentState
) -> bool:
    verdict = exhibit["verdict"]
    tracking = exhibit["mlflow"]
    run = state.get("mlflow_run") or {}
    assert isinstance(verdict, dict) and isinstance(tracking, dict)
    return bool(
        verdict.get("runs_found")
        or (
            tracking.get("configured")
            and not tracking.get("available")
            and run.get("run_id")
        )
    )


__all__ = ["ExhibitBuilder", "ExperimentExhibits", "should_pin_exhibit"]

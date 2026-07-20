"""Cross-component read models shared by delivery surfaces."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..artifacts.facade import build_experiment_figure
from .experiments.tracking_policy import mlflow_experiment_name

Record = dict[str, Any]
RecordQuery = Callable[..., Record]
RecordsQuery = Callable[..., list[Record]]


class TrackingOverview(Protocol):
    def health(self) -> dict[str, object]: ...

    def results_metrics(
        self, *, project_id: str, experiment_id: str, include_history: bool = True
    ) -> Record: ...

    def namespace_experiments(self, *, project_id: str) -> list[Record]: ...


@dataclass(slots=True)
class HomeQuery:
    """Assemble the project home read model without a delivery dependency."""

    experiments: RecordQuery
    resources: RecordQuery
    status_and_next: RecordQuery
    active_work: RecordQuery
    review_queue: RecordQuery
    recent_events: RecordQuery
    health: Callable[[], dict[str, object]]

    def __call__(self, *, project_id: str) -> Record:
        status = self.status_and_next(project_id=project_id)
        resources = self.resources(project_id=project_id)["resources"]
        reviews = self.review_queue(project_id=project_id)
        events = self.recent_events(project_id=project_id, limit=25)["events"]
        claims = status["project"]["active_claims"]
        experiments = self.experiments(project_id=project_id)["experiments"]
        work = self.active_work(project_id=project_id)
        active_experiments = work["active_experiments"]
        active_processes = work["active_processes"]
        active_experiment = active_experiments[0] if active_experiments else None
        return {
            "project": status["project"],
            "claims": claims,
            "experiments": experiments,
            "active_experiments": active_experiments,
            "active_processes": active_processes,
            "resources": resources,
            "reviews": reviews,
            "pending_change_sets": [],
            "recent_events": events,
            "stats": {
                "claims": len(claims),
                "experiments": len(experiments),
                "active_experiments": len(active_experiments),
                "active_processes": len(active_processes),
                "resources": len(resources),
                "open_reviews": len(reviews["requests"]),
            },
            "workflow": active_experiment.get("workflow") if active_experiment else status["workflow"],
            "active_experiment": active_experiment,
            "mlflow": self.health(),
        }


@dataclass(slots=True)
class MlflowOverviewQuery:
    """Join Research experiments to their external tracking read models."""

    experiments: RecordQuery
    tracking: TrackingOverview

    def experiment_metrics(self, *, project_id: str, experiment_id: str) -> Record:
        return self.tracking.results_metrics(
            project_id=project_id, experiment_id=experiment_id
        )

    def __call__(self, *, project_id: str) -> Record:
        health = self.tracking.health()
        unreachable = health.get("reachable") is False
        items: list[Record] = []
        for experiment in self.experiments(project_id=project_id)["experiments"]:
            experiment_id = str(experiment.get("id") or "")
            if not experiment_id:
                continue
            metrics = (
                {
                    "experiment_id": experiment_id,
                    "available": False,
                    "source": "mlflow",
                    "hint": "MLflow unreachable.",
                }
                if unreachable
                else self.tracking.results_metrics(
                    project_id=project_id,
                    experiment_id=experiment_id,
                    include_history=False,
                )
            )
            items.append(
                {
                    "experiment_id": experiment_id,
                    "name": experiment.get("name") or experiment_id,
                    "status": experiment.get("status") or "",
                    "intent": experiment.get("intent") or "",
                    "mlflow_experiment_name": mlflow_experiment_name(
                        project_id=project_id, experiment_id=experiment_id
                    ),
                    "dashboard_experiment_url": metrics.get("dashboard_experiment_url", ""),
                    "metrics": metrics,
                }
            )
        expected_names = {str(item["mlflow_experiment_name"]) for item in items}
        namespace = [] if unreachable else self.tracking.namespace_experiments(
            project_id=project_id
        )
        return {
            "mlflow": health,
            "experiments": items,
            "unmapped_mlflow_experiments": [
                experiment
                for experiment in namespace
                if str(experiment.get("name") or "") not in expected_names
            ],
        }


@dataclass(slots=True)
class ExperimentFigureQuery:
    """Gather component facts and build one derived experiment figure."""

    experiment_state: RecordQuery
    review_snapshot: RecordQuery
    open_reviews: RecordsQuery
    sandbox_row: Callable[..., Record | None]
    sandbox_view: RecordQuery
    sandbox_status_active: Callable[[str], bool]

    def __call__(self, *, project_id: str, experiment_id: str) -> Record:
        experiment = self.experiment_state(
            experiment_id=experiment_id, project_id=project_id
        )
        review_attempts = {}
        for review in experiment.get("reviews", []):
            snapshot = self.review_snapshot(
                snapshot_id=str(review.get("target_snapshot_id") or "")
            )
            review_attempts[str(review.get("id"))] = int(snapshot.get("attempt_index") or 0)
        row = self.sandbox_row(experiment_id=experiment_id, project_id=project_id)
        sandbox = self.sandbox_view(row=row) if row is not None else None
        return build_experiment_figure(
            experiment=experiment,
            review_attempts=review_attempts,
            open_review_requests=self.open_reviews(
                project_id=project_id, experiment_id=experiment_id
            ),
            sandbox=sandbox,
            sandbox_active=bool(
                sandbox
                and self.sandbox_status_active(str(sandbox.get("status") or ""))
            ),
        )

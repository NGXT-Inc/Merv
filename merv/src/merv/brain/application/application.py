# If you update this file, you must consult application.md to see whether application.md needs to be updated. application.md must not exceed 100 lines.
"""The concrete cross-module Application root.

Surface calls this object only for operations that coordinate multiple brain
modules.  Module-local operations continue to call their owning public root.
This first consolidation keeps the existing behavior intact while removing
the composition-wide bag of one-use Application objects.
"""

from __future__ import annotations

from typing import Any

from merv.shared.storage_guidance import storage_guidance

from ..artifacts import Artifacts
from ..feed import FeedService
from ..kernel.utils import ValidationError
from ..object_storage import ObjectStorage
from ..research_core import Research
from ..sandbox import SandboxEngine
from .experiments.context import ExperimentContextQuery
from .experiments.create import create_experiment
from .experiments.exhibits import ExperimentExhibits
from .experiments.presentation import (
    review_body,
    rich_experiment_state,
    slim_experiment_state,
)
from .experiments.transition import TransitionExperiment
from .mlflow import ExperimentTracking, MlflowIntegration
from .project_context import ProjectContextQuery
from .queries import LogicGraphQuery
from .reflections import (
    present_agent_reflection_state,
    present_reflection_overview,
)
from .reviews import (
    read_review_status,
    request_review,
    review_queue,
    start_review,
)
from .status_guidance import StatusGuidancePolicy
from .workflow import (
    StatusAndNextQuery,
    artifact_list_record,
    project_at_a_glance,
)


class Application:
    """Coordinate workflows spanning Research and one or more sibling modules."""

    def __init__(
        self,
        *,
        research: Research,
        artifacts: Artifacts,
        feed: FeedService,
        sandboxes: SandboxEngine,
        objects: ObjectStorage,
        tracking: ExperimentTracking | None = None,
    ) -> None:
        self.research = research
        self.artifacts = artifacts
        self.feed = feed
        self.sandboxes = sandboxes
        self.objects = objects
        self._mlflow = MlflowIntegration(
            research=research,
            feed=feed,
            objects=objects,
            adapter=tracking,
        )

        self._project_context = ProjectContextQuery(
            research=research,
            artifacts=artifacts,
        )
        self._experiment_context = ExperimentContextQuery(artifacts=artifacts)
        self._exhibits = ExperimentExhibits(
            research=research,
            artifacts=artifacts,
            mlflow=self._mlflow,
        )
        self._transition = TransitionExperiment(
            research=research,
            artifacts=artifacts,
            feed=feed,
            mlflow=self._mlflow,
            exhibits=self._exhibits,
            objects=objects,
        )
        self._policy = StatusGuidancePolicy(
            storage_enabled=bool(getattr(objects, "enabled", False)),
            storage_guidance=storage_guidance(
                enabled=bool(getattr(objects, "enabled", False))
            ),
        )
        self._workflow = StatusAndNextQuery(
            research=research,
            sandboxes=sandboxes,
            policy=self._policy,
            objects=objects,
            context=self._experiment_context,
            project_context=self._project_context,
        )
        self._graphs = LogicGraphQuery(research=research, artifacts=artifacts)

    # Workflow and context -------------------------------------------------

    def status(
        self, *, project_id: str | None = None, experiment_id: str | None = None
    ) -> dict[str, Any]:
        return self._workflow.status_and_next(
            project_id=project_id,
            experiment_id=experiment_id,
        )

    def status_for_agent(
        self, *, project_id: str | None = None, experiment_id: str | None = None
    ) -> dict[str, Any]:
        return self._workflow.status_and_next_agent(
            project_id=project_id,
            experiment_id=experiment_id,
        )

    def project_context(self, *, project_id: str | None = None) -> dict[str, Any]:
        return self._project_context.build(project_id=project_id)

    def project_list(
        self, *, user_id: str = "", project_id: str = ""
    ) -> dict[str, Any]:
        return self._reachable_projects(
            user_id=user_id,
            key_project_id=project_id,
        )

    def project(
        self,
        *,
        action: str,
        project_id: str = "",
        name: str = "",
        summary: str = "",
        tenant_id: str | None = None,
        user_id: str = "",
        key_project_id: str = "",
    ) -> dict[str, Any]:
        if action == "list":
            return self._reachable_projects(
                user_id=user_id,
                key_project_id=key_project_id,
            )
        if action == "current":
            if not key_project_id:
                return {
                    "exists": False,
                    "hint": (
                        "This credential reaches every project listed here, so "
                        "there is no single current project. Pass project_id "
                        "explicitly on each call."
                    ),
                    **self._reachable_projects(
                        user_id=user_id,
                        key_project_id=key_project_id,
                    ),
                }
            project = self.research.get_project(project_id=key_project_id)
            return {
                "exists": True,
                "project": {
                    "id": project["id"],
                    "name": project["name"],
                    "summary": project.get("summary", ""),
                },
            }
        if action == "create":
            return self.research.create_project(
                name=name,
                summary=summary,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        if action == "overview":
            resolved = project_id or key_project_id
            if not resolved:
                raise ValidationError(
                    "project_id is required: this credential is not bound to a "
                    'single project. Call project(action="list") to see the '
                    "projects you can work in, then pass project_id explicitly.",
                    details={"field": "project_id"},
                )
            return self.project_context(project_id=resolved)
        raise ValidationError(f'action="{action}" is not recognized for project')

    def _reachable_projects(
        self, *, user_id: str, key_project_id: str
    ) -> dict[str, Any]:
        listed = self.research.reachable_projects(
            user_id=user_id,
            key_project_id=key_project_id,
        )["projects"]
        return {
            "projects": [
                {
                    "id": project["id"],
                    "name": project["name"],
                    "summary": project.get("summary", ""),
                    "status": project.get("status", ""),
                    "created_at": project.get("created_at", ""),
                }
                for project in listed
            ]
        }

    def experiment_context(
        self,
        *,
        state: dict[str, Any],
        project_id: str | None = None,
        pinned_artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._experiment_context.build(
            state=state,
            project_id=project_id,
            pinned_artifacts=pinned_artifacts,
        )

    # Experiments ----------------------------------------------------------

    def create_experiment(self, **kwargs: Any) -> dict[str, Any]:
        return create_experiment(self.research, **kwargs)

    def experiments(
        self, *, project_id: str | None = None, rich: bool = False
    ) -> dict[str, Any] | list[dict[str, Any]]:
        states = self.research.project_experiments(project_id=project_id)
        ids = tuple(str(state.get("id") or "") for state in states if state.get("id"))
        resolved = (
            str(states[0].get("project_id") or project_id or "") if states else ""
        )
        objects = (
            self.objects.by_experiment(project_id=resolved, experiment_ids=ids)
            if ids
            else {}
        )
        presented = [
            (rich_experiment_state if rich else slim_experiment_state)(
                state,
                storage_objects=objects.get(str(state.get("id") or ""), []),
            )
            for state in states
        ]
        return presented if rich else {"experiments": presented}

    def list_experiments(self, *, project_id: str | None = None) -> dict[str, Any]:
        return self.experiments(project_id=project_id, rich=False)

    def experiment(
        self,
        *,
        experiment_id: str,
        project_id: str | None = None,
        review_id: str = "",
        rich: bool = False,
    ) -> dict[str, Any]:
        if rich:
            state = self.research.experiment_state(
                experiment_id=experiment_id,
                project_id=project_id,
            )
            resolved_project_id = str(state.get("project_id") or project_id or "")
            response = rich_experiment_state(
                state,
                storage_objects=self.objects.by_experiment(
                    project_id=resolved_project_id,
                    experiment_ids=(experiment_id,),
                )[experiment_id],
                include_legacy_tracking=self._mlflow.enabled,
            )
            if not self._mlflow.enabled:
                response.pop("mlflow_run", None)
            else:
                self._mlflow.decorate(
                    response,
                    project_id=resolved_project_id,
                    experiment_id=experiment_id,
                    include_credentials=False,
                    include_guidance=False,
                )
            return response
        state = self.research.experiment_state(
            experiment_id=experiment_id,
            project_id=project_id,
        )
        resolved_project_id = str(state.get("project_id") or project_id or "")
        response = slim_experiment_state(
            state,
            storage_objects=self.objects.by_experiment(
                project_id=resolved_project_id,
                experiment_ids=(experiment_id,),
            )[experiment_id],
            include_legacy_tracking=self._mlflow.enabled,
        )
        if review_id:
            body = review_body(state.get("reviews", []), review_id=review_id)
            if body is None:
                known = [
                    str(review.get("id") or "") for review in state.get("reviews", [])
                ]
                raise ValidationError(
                    f"no review {review_id} on this experiment. Reviews here: "
                    f"{', '.join(known) or 'none yet'}.",
                    details={"field": "review_id", "review_ids": known},
                )
            response["review"] = body
        return self._mlflow.decorate(
            response,
            project_id=resolved_project_id,
            experiment_id=experiment_id,
            include_credentials=True,
        )

    def transition_experiment(
        self,
        *,
        experiment_id: str,
        transition: str,
        evidence: dict[str, Any] | None = None,
        project_id: str | None = None,
        rich: bool = False,
    ) -> dict[str, Any]:
        operation = self._transition.execute if rich else self._transition.agent
        return operation(
            experiment_id=experiment_id,
            transition=transition,
            evidence=evidence,
            project_id=project_id,
        )

    def exhibit(self, *, project_id: str, experiment_id: str) -> dict[str, Any]:
        return self._exhibits.preview(
            project_id=project_id,
            experiment_id=experiment_id,
        )

    # Reviews and reflections ---------------------------------------------

    def request_review(
        self,
        *,
        target_type: str,
        target_id: str,
        role: str,
        reason: str = "",
        producer_session_id: str = "main",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return request_review(
            self.research,
            target_type=target_type,
            target_id=target_id,
            role=role,
            reason=reason,
            producer_session_id=producer_session_id,
            project_id=project_id,
        )

    def start_review(
        self,
        *,
        review_request_id: str,
        reviewer_capability: str,
        declared_agent: str = "",
        caller_session_id: str = "",
    ) -> dict[str, Any]:
        return start_review(
            research=self.research,
            artifacts=self.artifacts,
            experiment_context=self._experiment_context,
            project_context=self._project_context,
            review_request_id=review_request_id,
            reviewer_capability=reviewer_capability,
            declared_agent=declared_agent,
            caller_session_id=caller_session_id,
        )

    def review_status(
        self,
        *,
        target_type: str,
        target_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return read_review_status(
            research=self.research,
            feed=self.feed,
            target_type=target_type,
            target_id=target_id,
            project_id=project_id,
        )

    def review_queue(self, *, project_id: str | None = None) -> dict[str, Any]:
        return review_queue(self.research, project_id=project_id)

    def create_reflection(
        self,
        *,
        project_id: str,
        title: str = "",
        lenses: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return present_agent_reflection_state(
            self.research.create_reflection(
                project_id=project_id,
                title=title,
                lenses=lenses or [],
            ),
            include_content=False,
        )

    def reflection(
        self,
        *,
        project_id: str,
        reflection_id: str,
        include_content: bool = False,
    ) -> dict[str, Any]:
        return present_agent_reflection_state(
            self.research.reflection_state(
                project_id=project_id,
                reflection_id=reflection_id,
                include_content=True,
            ),
            include_content=include_content,
        )

    def reflections(self, *, project_id: str) -> dict[str, Any]:
        result = self.research.list_reflections(project_id=project_id)
        return present_reflection_overview(
            {
                "count": result.get(
                    "count",
                    len(result.get("reflections", [])),
                ),
                **result,
            }
        )

    def reflection_overview(self, *, project_id: str) -> dict[str, Any]:
        return present_reflection_overview(
            self.research.reflection_overview(project_id=project_id)
        )

    def transition_reflection(
        self,
        *,
        project_id: str,
        reflection_id: str,
        transition: str,
    ) -> dict[str, Any]:
        return present_agent_reflection_state(
            self.research.transition_reflection(
                project_id=project_id,
                reflection_id=reflection_id,
                transition=transition,
            ),
            include_content=False,
        )

    # Read models ----------------------------------------------------------

    def dashboard(self, *, project_id: str) -> dict[str, Any]:
        snapshot = self.research.snapshot(project_id=project_id)
        status, work, experiments = self._workflow.project_models(
            snapshot=snapshot,
            sandboxes=self.sandboxes.for_project(project_id=project_id),
        )
        artifacts = [
            artifact_list_record(artifact)
            for artifact in self.artifacts.scan(project_id=project_id)
        ]
        reviews = self.review_queue(project_id=project_id)
        claims = status["project"]["active_claims"]
        active_experiments = work["active_experiments"]
        active_processes = work["active_processes"]
        active = active_experiments[0] if active_experiments else None
        result = {
            "project": status["project"],
            "claims": claims,
            "experiments": experiments,
            "active_experiments": active_experiments,
            "active_processes": active_processes,
            "artifacts": artifacts,
            "reviews": reviews,
            "pending_change_sets": [],
            "recent_events": self.recent_events(
                project_id=project_id,
                limit=25,
            )["events"],
            "stats": {
                "claims": len(claims),
                "experiments": len(experiments),
                "active_experiments": len(active_experiments),
                "active_processes": len(active_processes),
                "artifacts": len(artifacts),
                "open_reviews": len(reviews["requests"]),
            },
            "workflow": active.get("workflow") if active else status["workflow"],
            "active_experiment": active,
        }
        health = self._mlflow.health()
        if health:
            result["mlflow"] = health
        return result

    def tracking_health(self) -> dict[str, Any]:
        return dict(self._mlflow.health())

    def current_project(self, *, tenant_id: str | None = None) -> dict[str, Any]:
        result = self.research.current_project(tenant_id=tenant_id)
        project = result.get("project") or {}
        project_id = str(project.get("id") or "")
        if not result.get("exists") or not project_id:
            return result
        return {
            **result,
            "at_a_glance": project_at_a_glance(
                self.research.snapshot(project_id=project_id)
            ),
        }

    def figure_facts(self, *, project_id: str, experiment_id: str) -> dict[str, Any]:
        """Gather cross-module facts; Surface owns the UI projection."""
        experiment = self.research.experiment_state(
            experiment_id=experiment_id,
            project_id=project_id,
        )
        review_attempts = {
            str(review.get("id")): int(
                self.research.review_snapshot(
                    snapshot_id=str(review.get("target_snapshot_id") or "")
                ).get("attempt_index")
                or 0
            )
            for review in experiment.get("reviews", [])
        }
        sandbox, active = self.sandboxes.figure_snapshot(
            experiment_id=experiment_id,
            project_id=project_id,
        )
        return {
            "experiment": experiment,
            "review_attempts": review_attempts,
            "open_review_requests": self.research.open_experiment_reviews(
                project_id=project_id,
                experiment_id=experiment_id,
            ),
            "sandbox": sandbox,
            "sandbox_active": active,
        }

    def compute_cost(self, *, project_id: str) -> dict[str, Any]:
        spend = self.sandboxes.project_spend(project_id=project_id)
        names = {
            str(experiment.get("id") or ""): str(experiment.get("name") or "")
            for experiment in self.research.project_experiment_summaries(
                project_id=project_id
            )
        }
        for entry in spend["by_experiment"]:
            entry["experiment_name"] = names.get(entry["experiment_id"], "")
        return spend

    def tenant_counters(self, *, tenant_id: str) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "tool_calls": self.research.tenant_event_count(tenant_id=tenant_id),
            **self.sandboxes.tenant_generation_counters(tenant_id=tenant_id),
        }

    def timeline_signal(self, *, project_id: str) -> str:
        return self.research.project_event_signal(project_id=project_id)

    def recent_events(self, *, project_id: str, limit: int) -> dict[str, Any]:
        result = self.research.recent_events(project_id=project_id, limit=500)
        return {
            **result,
            "events": _visible_events(result.get("events") or [])[:limit],
        }

    def events_since(self, *, project_id: str, after_id: int) -> dict[str, Any]:
        result = self.research.events_since(
            project_id=project_id,
            after_id=after_id,
        )
        return {
            **result,
            "events": _visible_events(result.get("events") or []),
        }

    def experiment_graph(
        self, *, project_id: str, experiment_id: str
    ) -> dict[str, Any]:
        return self._graphs.experiment(
            project_id=project_id,
            experiment_id=experiment_id,
        )

    def project_graph(self, *, project_id: str) -> dict[str, Any]:
        return self._graphs.project(project_id=project_id)

    def reflection_graph(
        self, *, project_id: str, reflection_id: str
    ) -> dict[str, Any]:
        return self._graphs.reflection_graph(
            project_id=project_id,
            reflection_id=reflection_id,
        )

    # Optional MLflow integration -----------------------------------------

    @property
    def tracking_enabled(self) -> bool:
        return self._mlflow.enabled

    def tracking_context(
        self, *, project_id: str, experiment_id: str | None = None
    ) -> dict[str, Any]:
        return self._mlflow.context(
            project_id=project_id,
            experiment_id=experiment_id,
        )

    def finalize_tracking(
        self,
        *,
        project_id: str,
        experiment_id: str,
        run_id: str | None = None,
        status: str | None = "FINISHED",
        wait_seconds: float = 2.0,
    ) -> dict[str, Any]:
        return self._mlflow.finalize(
            project_id=project_id,
            experiment_id=experiment_id,
            run_id=run_id,
            status=status,
            wait_seconds=wait_seconds,
        )

    def tracking_overview(self, *, project_id: str) -> dict[str, Any]:
        return self._mlflow.overview(project_id=project_id)

    def tracking_metrics(
        self, *, project_id: str, experiment_id: str
    ) -> dict[str, Any]:
        return self._mlflow.metrics(
            project_id=project_id,
            experiment_id=experiment_id,
        )


__all__ = ["Application"]


def _visible_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hide dormant integration events and fields without deleting history."""
    return [
        _strip_legacy_fields(event)
        for event in events
        if "mlflow" not in str(event.get("type") or "").lower()
    ]


def _strip_legacy_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_legacy_fields(item)
            for key, item in value.items()
            if "mlflow" not in str(key).lower()
        }
    if isinstance(value, list):
        return [_strip_legacy_fields(item) for item in value]
    return value

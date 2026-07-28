"""Producer-facing review queries and their event-keyed response reactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..research_core.facade import (
    ResearchCore,
    ResearchReviewDelivery,
    ResearchReviews,
)
from .events import EventDispatcher
from .experiments.context import ExperimentContextQuery
from .project_context import ProjectContextQuery
from .reflections import ReflectionCommands


@dataclass(kw_only=True, eq=False, repr=False)
class StartReviewSession:
    """Start a pinned review, then attach bounded orientation for its target."""

    reviews: ResearchReviewDelivery
    research: ResearchCore
    experiment_context: ExperimentContextQuery
    project_context: ProjectContextQuery
    reflections: ReflectionCommands

    def execute(
        self,
        *,
        review_request_id: str,
        reviewer_capability: str,
        declared_agent: str = "",
        caller_session_id: str = "",
    ) -> dict[str, Any]:
        result = dict(
            self.reviews.start(
                review_request_id=review_request_id,
                reviewer_capability=reviewer_capability,
                declared_agent=declared_agent,
                caller_session_id=caller_session_id,
            )
        )
        project_id = str(result.get("project_id") or "")
        target_type = str(result.get("target_type") or "")
        target_id = str(result.get("target_id") or "")
        target_snapshot = result.pop("target_snapshot", {})
        result["project_context"] = self.project_context.build(
            project_id=project_id
        )
        if target_type == "experiment":
            submitted_artifacts = result.pop("submitted_artifacts", [])
            live_state = self.research.experiment_state(
                experiment_id=target_id, project_id=project_id
            )
            state = {
                **live_state,
                "status": target_snapshot.get("status")
                or live_state.get("status"),
                "attempt_index": target_snapshot.get("attempt_index")
                or live_state.get("attempt_index"),
            }
            result["context"] = self.experiment_context.build(
                state=state,
                project_id=project_id,
                pinned_artifacts=submitted_artifacts,
            )
        elif target_type == "reflection":
            result["reflection_context"] = self.reflections.get(
                project_id=project_id,
                reflection_id=target_id,
            )
        return result


@dataclass(kw_only=True, eq=False, repr=False)
class ReadReviewStatus:
    """Read canonical review state, then attach best-effort producer guidance."""

    research: ResearchCore
    reviews: ResearchReviews
    dispatcher: EventDispatcher

    def execute(
        self, *, target_type: str, target_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        result = self.reviews.status(
            target_type=target_type, target_id=target_id, project_id=project_id
        )
        if target_type != "experiment" or not result.get("reviews"):
            return result
        try:
            state = self.research.experiment_state(
                experiment_id=target_id, project_id=project_id
            )
            event = self.reviews.latest_submitted_event(
                target_type=target_type,
                target_id=target_id,
                project_id=str(state.get("project_id") or project_id or ""),
            )
        except Exception:  # project/event enrichment is advisory, unlike the status read
            return result
        if event is None:
            return result
        reacted = self.dispatcher.dispatch(event=event, phase="producer_read", state=state)
        note = reacted.outcomes.get("feed")
        if note is not None:
            result["feed_note"] = note
        return result


__all__ = ["ReadReviewStatus", "StartReviewSession"]

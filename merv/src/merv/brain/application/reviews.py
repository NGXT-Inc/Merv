"""Producer-facing review queries and their event-keyed response reactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..research_core.facade import ResearchCore, ResearchReviews
from .events import EventDispatcher


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


__all__ = ["ReadReviewStatus"]

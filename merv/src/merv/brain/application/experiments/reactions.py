"""Explicit reactions shared by experiment-facing application use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from ...feed.facade import Feed
from ...research_core.facade import ExperimentState, PersistedRunState, ResearchCore
from ..events import (
    EventCatalogEntry,
    EventContext,
    EventDispatcher,
    EventReaction,
    FailureMode,
    IdempotencyMode,
)
from ..ports.tracking import ExperimentTracking, TRACKING_TERMINAL_RUN_STATUSES


_FINAL_TRACKING_STATUS = {
    "submit_results": "FINISHED",
    "complete": "FINISHED",
    "abandon": "KILLED",
    "mark_failed": "FAILED",
}
_RUN_FIELDS = (
    "run_id", "run_name", "status", "artifact_uri",
    "created_at", "created_by_plugin", "error",
)


_TRANSITION = "merv.brain.research_core.experiments.ExperimentService.transition_with_event"
_REVIEW = "merv.brain.research_core.reviews.ReviewService.submit"
_REFRESH = "merv.brain.research_core.experiments.ExperimentService.record_mlflow_run"


def _reaction(
    producer: str, event_type: str, phase: str, handler: str, *,
    failure: FailureMode = "advisory",
    idempotency: IdempotencyMode = "repeat_safe",
) -> EventCatalogEntry:
    return EventCatalogEntry(
        producer, event_type, 1, producer,
        phase, handler, failure, idempotency,
    )


EXPERIMENT_REACTION_CATALOG = (
    _reaction(
        _TRANSITION, "experiment.transitioned", "post_commit", "tracking_start",
        failure="fatal",
        idempotency="requires_adapter_key_for_redelivery",
    ),
    _reaction(
        _TRANSITION, "experiment.transitioned", "post_commit", "tracking_finalize",
    ),
    _reaction(_TRANSITION, "experiment.transitioned", "post_response", "feed"),
    _reaction(_REVIEW, "review.submitted", "producer_read", "feed"),
    _reaction(_REFRESH, "experiment.mlflow_run_refreshed", "post_response", "feed"),
)


@dataclass(kw_only=True, eq=False, repr=False)
class ExperimentReactions:
    """Synchronous experiment reactions bound once by application composition."""

    research: ResearchCore
    feed: Feed
    tracking: ExperimentTracking | None

    def bind(self, registry: EventDispatcher) -> None:
        registry.bind_catalog(
            EXPERIMENT_REACTION_CATALOG,
            handlers={
                "tracking_start": self.tracking_start,
                "tracking_finalize": self.tracking_finalize,
                "feed": self.feed_advisory,
            },
        )

    def tracking_start(
        self, context: EventContext[ExperimentState]
    ) -> EventReaction[ExperimentState]:
        transition = str(context.event.payload.get("transition") or "")
        state = context.state
        if transition in ("start_running", "retry_running"):
            state = self._ensure_tracking_run(
                state=state,
                replace_terminal=transition == "retry_running",
            )
        return EventReaction(state=state)

    def tracking_finalize(
        self, context: EventContext[ExperimentState]
    ) -> EventReaction[ExperimentState]:
        state = context.state
        if requested := _FINAL_TRACKING_STATUS.get(
            str(context.event.payload.get("transition") or "")
        ):
            state = self._finalize_tracking_run(state=state, status=requested)
        return EventReaction(state=state)

    def _ensure_tracking_run(
        self, *, state: ExperimentState, replace_terminal: bool
    ) -> ExperimentState:
        if self.tracking is None:
            return state
        capabilities = self.tracking.capabilities()
        if not (capabilities.logging and capabilities.control):
            return state
        existing = state.get("mlflow_run") or {}
        persisted_status = str(existing.get("status") or "").upper()
        if existing.get("run_id") and (
            not replace_terminal
            or persisted_status not in TRACKING_TERMINAL_RUN_STATUSES
        ):
            return state
        experiment_id = str(state.get("id") or "")
        project_id = str(state.get("project_id") or "")
        attempt_index = int(state.get("attempt_index") or 1)
        created = self.tracking.create_run(
            project_id=project_id,
            experiment_id=experiment_id,
            attempt_index=attempt_index,
            run_name=f"{experiment_id}-attempt-{attempt_index}",
        )
        if not (created.get("run_id") or created.get("error")):
            return state
        return self.research.record_tracking_run(
            project_id=project_id,
            experiment_id=experiment_id,
            run=_persisted_run(created),
        )

    def _finalize_tracking_run(
        self, *, state: ExperimentState, status: str
    ) -> ExperimentState:
        run = state.get("mlflow_run") or {}
        run_id = str(run.get("run_id") or "")
        if (
            self.tracking is None
            or not run_id
            or not run.get("created_by_plugin")
            or str(run.get("status") or "").upper() in TRACKING_TERMINAL_RUN_STATUSES
        ):
            return state
        finalized = self.tracking.finalize_run(
            project_id=str(state.get("project_id") or ""),
            experiment_id=str(state.get("id") or ""),
            run_id=run_id,
            status=status,
            wait_seconds=0.0,
        )
        readback = finalized.get("run")
        if isinstance(readback, dict) and str(readback.get("run_id") or "") == run_id:
            return self.research.record_tracking_run(
                project_id=str(state.get("project_id") or ""),
                experiment_id=str(state.get("id") or ""),
                run=_persisted_run(readback),
                event_type="experiment.mlflow_run_refreshed",
            )
        return state

    def feed_advisory(
        self, context: EventContext[ExperimentState]
    ) -> EventReaction[ExperimentState]:
        if context.event.type == "review.submitted":
            event = "experiment_review_verdict"
        elif context.event.type == "experiment.mlflow_run_refreshed":
            event = "mlflow_run_finalized"
        else:
            status = str(context.state.get("status") or "")
            event = (
                f"experiment_{status}"
                if status in ("complete", "failed", "abandoned")
                else None
            )
        if event is None or context.event.target_type != "experiment":
            return EventReaction(state=context.state)
        note = self.feed.transition_advisory(
            project_id=str(context.state.get("project_id") or ""),
            experiment_id=str(context.state.get("id") or ""),
            event=event,
        )
        return EventReaction(state=context.state, value=note)


def _persisted_run(run: dict[str, Any]) -> PersistedRunState:
    persisted = {key: run[key] for key in _RUN_FIELDS if key in run}
    if "created" in run:
        persisted["created_by_plugin"] = bool(run["created"])
    return cast(PersistedRunState, persisted)


__all__ = ["EXPERIMENT_REACTION_CATALOG", "ExperimentReactions"]

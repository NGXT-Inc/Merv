"""Explicit reactions shared by experiment-facing application use cases."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

from merv.shared.errors import TrackingPersistenceError

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
from ..ports.tracking import (
    CreateRunResult,
    ExperimentTracking,
    TRACKING_TERMINAL_RUN_STATUSES,
)
from .tracking_presentation import tracking_failure_message, tracking_warning


LOGGER = logging.getLogger(__name__)


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

# Tracking availability is a deployment precondition (MERV_REQUIRE_AGENT_MLFLOW
# is validated at startup), never a per-transition one: the transition is
# already committed here, so an outage becomes durable state plus this warning.
_TRACKING_REPAIR = (
    "The transition is committed; only MLflow tracking degraded. No "
    "plugin-created run is attached to this attempt. Read mlflow.context for "
    "the logging environment, log to a run you create there, then call "
    "mlflow.finalize_run with that run_id to attach it to the experiment."
)
# Losing the durable tracking outcome is a different failure domain from an
# MLflow outage: nothing reliably records what happened, so the caller gets the
# error. A failed write can still have committed (a lost acknowledgement), so
# the claim is honestly uncertain rather than an assertion of absence.
_PERSISTENCE_FAILURE = (
    "The experiment transition already committed and must not be retried, but "
    "persisting its MLflow tracking outcome failed on both attempts, so a "
    "durable record of the run (or of the outage) may or may not exist. Verify "
    "with experiment.get_state before any repair, then attach any run you find "
    "with mlflow.finalize_run. Underlying error: "
)


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
        failure="degraded",
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
        if transition not in ("start_running", "retry_running"):
            return EventReaction(state=context.state)
        try:
            state, attempted = self._ensure_tracking_run(
                state=context.state,
                replace_terminal=transition == "retry_running",
                delivery_id=context.event.id,
            )
        except TrackingPersistenceError:
            raise  # A durable outcome is this handler's promise; silence is worse.
        except Exception as exc:  # noqa: BLE001 - the commit must not be masked
            LOGGER.error(
                "MLflow tracking degraded after a committed %s on experiment %s: %s",
                transition, context.event.target_id, _message(exc),
            )
            return EventReaction(state=context.state, value=_warning(_message(exc)))
        if not attempted:
            return EventReaction(state=state)
        run = state.get("mlflow_run") or {}
        error = "" if run.get("run_id") else str(run.get("error") or "")
        return EventReaction(state=state, value=_warning(error) if error else None)

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
        self, *, state: ExperimentState, replace_terminal: bool, delivery_id: int
    ) -> tuple[ExperimentState, bool]:
        """Return the state plus whether a tracking outcome is this call's to report."""
        if self.tracking is None:
            return state, False
        capabilities = self.tracking.capabilities()
        if not (capabilities.logging and capabilities.control):
            return state, False
        existing = state.get("mlflow_run") or {}
        persisted_status = str(existing.get("status") or "").upper()
        if existing.get("run_id") and (
            not replace_terminal
            or persisted_status not in TRACKING_TERMINAL_RUN_STATUSES
        ):
            return state, False
        experiment_id = str(state.get("id") or "")
        project_id = str(state.get("project_id") or "")
        # Redelivery of an already-served event: the guard above only catches
        # the run-id shape, so an error-only outcome would otherwise create a
        # second MLflow run before the writer's barrier discarded the write.
        # Reporting the durable outcome again reproduces the original answer.
        if (served := self._landed_tracking_run(
            project_id=project_id, experiment_id=experiment_id,
            delivery_id=delivery_id,
        )) is not None:
            LOGGER.warning(
                "Redelivered MLflow tracking event for experiment %s (delivery "
                "%s) already has a durable outcome; not creating a second run.",
                experiment_id, delivery_id,
            )
            return served, True
        attempt_index = int(state.get("attempt_index") or 1)
        created: CreateRunResult
        try:
            created = self.tracking.create_run(
                project_id=project_id,
                experiment_id=experiment_id,
                attempt_index=attempt_index,
                run_name=f"{experiment_id}-attempt-{attempt_index}",
            )
        except Exception as exc:  # noqa: BLE001 - an outage becomes durable state
            created = {"error": f"MLflow run creation failed: {_message(exc)}"}
        if not (created.get("run_id") or created.get("error")):
            return state, True
        # The degrade path is a real incident: log the cause here, where it is
        # converted to a payload, so it survives even if persisting it fails.
        adapter_failure = (
            "" if created.get("run_id") else str(created.get("error") or "")
        )
        if adapter_failure:
            LOGGER.error(
                "MLflow run creation failed for experiment %s; recording the "
                "outage as this attempt's durable tracking state: %s",
                experiment_id, adapter_failure,
            )
        persisted = self._persist_tracking_run(
            project_id=project_id,
            experiment_id=experiment_id,
            run=_persisted_run(created),
            adapter_failure=adapter_failure,
            delivery_id=delivery_id,
        )
        return persisted, True

    def _persist_tracking_run(
        self, *, project_id: str, experiment_id: str, run: PersistedRunState,
        delivery_id: int, adapter_failure: str = "",
    ) -> ExperimentState:
        """Write the tracking outcome durably, retrying once before failing loud."""
        try:
            return self.research.record_tracking_run(
                project_id=project_id, experiment_id=experiment_id, run=run,
                delivery_id=delivery_id,
            )
        except Exception as first:  # noqa: BLE001 - retryable unless it landed
            LOGGER.error(
                "Retrying the durable MLflow tracking outcome for experiment %s: %s",
                experiment_id, _message(first),
            )
        # A failed write can still have committed (a lost acknowledgement), and
        # the tracking event is unconstrained: read the ledger for THIS
        # delivery's key before writing again, so an ambiguous commit stays one
        # durable record, not two. The retry re-checks the same key inside its
        # own transaction, so this read is the fast path that also names the
        # currently durable row — never the barrier that guarantees one append.
        if (landed := self._landed_tracking_run(
            project_id=project_id, experiment_id=experiment_id,
            delivery_id=delivery_id,
        )) is not None:
            # Name the row that is actually current: a concurrent delivery may
            # have superseded this one, and the caller must read the database's
            # truth rather than this delivery's intent.
            LOGGER.error(
                "The failed MLflow tracking write for experiment %s (delivery %s) "
                "is durable after all; skipping the retry that would duplicate "
                "it. Current durable run: %s.",
                experiment_id, delivery_id,
                str((landed.get("mlflow_run") or {}).get("run_id") or "") or "none",
            )
            return landed
        try:
            return self.research.record_tracking_run(
                project_id=project_id, experiment_id=experiment_id, run=run,
                delivery_id=delivery_id,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised as a server error below
            orphan = str(run.get("run_id") or "")
            cause = _message(exc)
            LOGGER.error(
                "MLflow tracking outcome for experiment %s may never have reached "
                "the database (orphaned run: %s): %s%s",
                experiment_id, orphan or "none", cause,
                f" (after {adapter_failure})" if adapter_failure else "",
            )
            raise TrackingPersistenceError(
                f"{_PERSISTENCE_FAILURE}{cause}"
                + (f" (possibly orphaned MLflow run: {orphan})" if orphan else "")
                + (f". The outcome it was recording: {adapter_failure}"
                   if adapter_failure else "")
            ) from exc

    def _landed_tracking_run(
        self, *, project_id: str, experiment_id: str, delivery_id: int
    ) -> ExperimentState | None:
        """Return the durable state when THIS delivery's write committed.

        Correlation is the delivery id carried in the append-only event, never
        the mutable current row: an identical run id or adapter error from an
        earlier delivery is not proof, and a concurrent delivery overwriting
        the row is not disproof.
        """
        try:
            return self.research.tracking_delivery_state(
                project_id=project_id,
                experiment_id=experiment_id,
                delivery_id=delivery_id,
            )
        except Exception as exc:  # noqa: BLE001 - the retry stays the fallback
            LOGGER.error(
                "Could not re-read the MLflow tracking ledger of experiment %s "
                "before retrying its write: %s",
                experiment_id, _message(exc),
            )
            return None

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


def _message(exc: BaseException) -> str:
    return tracking_failure_message(exc)


def _warning(error: str) -> dict[str, str]:
    return tracking_warning(error=error, repair=_TRACKING_REPAIR)


def _persisted_run(run: dict[str, Any]) -> PersistedRunState:
    persisted = {key: run[key] for key in _RUN_FIELDS if key in run}
    if "created" in run:
        persisted["created_by_plugin"] = bool(run["created"])
    return cast(PersistedRunState, persisted)


__all__ = [
    "EXPERIMENT_REACTION_CATALOG",
    "ExperimentReactions",
    "TrackingPersistenceError",
]

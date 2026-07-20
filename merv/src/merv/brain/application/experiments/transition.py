"""The experiment-transition application command and its event reactions."""

from __future__ import annotations

from contextlib import suppress
from typing import Any, TypedDict, cast

from merv.shared.artifact_roles import EXHIBIT_ROLE

from ...artifacts.facade import Artifacts
from ...feed.facade import Feed
from ...research_core.facade import (
    ExperimentState,
    PersistedRunState,
    ResearchCore,
    SlimExperimentState,
)
from ..events import EventContext, EventDispatcher, EventReaction
from ..ports.tracking import ExperimentTracking, TrackingContextPayload
from .exhibits import ExhibitBuilder, should_pin_exhibit
from .metrics_exhibit import METRICS_EXHIBIT_FILENAME, exhibit_bytes
from .tracking_policy import MLFLOW_TERMINAL_RUN_STATUSES
from .tracking_presentation import with_tracking_if_visible


class TransitionResponse(SlimExperimentState, total=False):
    mlflow: TrackingContextPayload
    mlflow_guidance: str
    metrics_exhibit: dict[str, object]
    feed_note: str


_FINAL_TRACKING_STATUS = {
    "submit_results": "FINISHED",
    "complete": "FINISHED",
    "abandon": "KILLED",
    "mark_failed": "FAILED",
}
_TERMINAL_FEED_EVENT = {
    "complete": "experiment_complete",
    "failed": "experiment_failed",
    "abandoned": "experiment_abandoned",
}
_RUN_FIELDS = (
    "run_id",
    "run_name",
    "status",
    "artifact_uri",
    "created_at",
    "created_by_plugin",
    "error",
)


class TransitionExperiment:
    """Coordinate one transition without exposing component internals."""

    def __init__(
        self,
        *,
        research: ResearchCore,
        artifacts: Artifacts,
        feed: Feed,
        tracking: ExperimentTracking | None,
        exhibits: ExhibitBuilder,
    ) -> None:
        self.research = research
        self.artifacts = artifacts
        self.feed = feed
        self.tracking = tracking
        self.exhibits = exhibits
        self.dispatcher = EventDispatcher()
        self.dispatcher.register(
            event_type="experiment.transitioned",
            phase="post_commit",
            name="tracking",
            handler=self._react_tracking,
        )
        self.dispatcher.register(
            event_type="experiment.transitioned",
            phase="post_response",
            name="feed",
            handler=self._react_feed,
        )

    def execute(
        self,
        *,
        experiment_id: str,
        transition: str,
        evidence: dict[str, Any] | None = None,
        project_id: str | None = None,
        include_tracking_credentials: bool = False,
    ) -> TransitionResponse:
        exhibit = None
        if transition == "submit_results":
            before = self.research.experiment_state(
                experiment_id=experiment_id, project_id=project_id
            )
            if str(before.get("status")) == "running":
                exhibit = self._finalize_exhibit(state=before)

        committed = self.research.transition_experiment(
            experiment_id=experiment_id,
            transition=transition,
            evidence=evidence,
            project_id=project_id,
        )
        reacted = self.dispatcher.dispatch(
            event=committed.event, phase="post_commit", state=committed.state
        )
        state = reacted.state
        resolved_project_id = str(state.get("project_id") or project_id or "")
        response = cast(TransitionResponse, dict(self.research.present_experiment(state)))
        with_tracking_if_visible(
            state=response,
            tracking=self.tracking,
            project_id=resolved_project_id,
            experiment_id=experiment_id,
            include_credentials=include_tracking_credentials,
        )
        if transition in ("start_running", "retry_running"):
            response["metrics_exhibit"] = self._exhibit_expectation(
                experiment_id=experiment_id, state=response
            )
        elif transition == "submit_results" and exhibit is not None:
            response["metrics_exhibit"] = {
                "pinned": True,
                "path": self._exhibit_path(experiment_id=experiment_id, state=response),
                "verdict": exhibit["verdict"],
            }

        late = self.dispatcher.dispatch(
            event=committed.event, phase="post_response", state=state
        )
        note = late.outcomes.get("feed")
        if isinstance(note, str):
            response["feed_note"] = note
        return response

    def _finalize_exhibit(
        self, *, state: ExperimentState
    ) -> dict[str, object] | None:
        exhibit = self.exhibits.generate(state=state)
        pinned = should_pin_exhibit(exhibit=exhibit, state=state)
        verdict = {
            **dict(exhibit["verdict"]),
            "attempt_index": exhibit["attempt_index"],
            "mlflow": exhibit["mlflow"],
            "pinned": pinned,
        }
        project_id = str(state.get("project_id") or "")
        experiment_id = str(state.get("id") or "")
        self.research.record_exhibit_verdict(
            experiment_id=experiment_id,
            project_id=project_id,
            verdict=verdict,
        )
        if not pinned:
            return None
        self.artifacts.pin_system_artifact(
            path=self._exhibit_path(experiment_id=experiment_id, state=state),
            experiment_id=experiment_id,
            role=EXHIBIT_ROLE,
            content_bytes=exhibit_bytes(exhibit),
            content_type="application/json",
            title="Metrics exhibit (system-generated)",
            kind="result",
            project_id=project_id,
        )
        return exhibit

    def _react_tracking(
        self, context: EventContext[ExperimentState]
    ) -> EventReaction[ExperimentState]:
        transition = str(context.event.payload.get("transition") or "")
        state = context.state
        if transition in ("start_running", "retry_running"):
            state = self._ensure_tracking_run(
                state=state,
                replace_terminal=transition == "retry_running",
            )
        elif requested := _FINAL_TRACKING_STATUS.get(transition):
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
            or persisted_status not in MLFLOW_TERMINAL_RUN_STATUSES
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
            or str(run.get("status") or "").upper() in MLFLOW_TERMINAL_RUN_STATUSES
        ):
            return state
        with suppress(Exception):
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

    def _react_feed(
        self, context: EventContext[ExperimentState]
    ) -> EventReaction[ExperimentState]:
        event = _TERMINAL_FEED_EVENT.get(str(context.state.get("status") or ""))
        if event is None:
            return EventReaction(state=context.state)
        try:
            note = self.feed.transition_advisory(
                project_id=str(context.state.get("project_id") or ""),
                experiment_id=str(context.state.get("id") or ""),
                event=event,
            )
        except Exception:  # advisory only
            note = None
        return EventReaction(state=context.state, value=note)

    def _exhibit_path(
        self, *, experiment_id: str, state: dict[str, Any]
    ) -> str:
        return self.research.exhibit_path(
            experiment_id=experiment_id,
            name=str(state.get("name") or ""),
            filename=METRICS_EXHIBIT_FILENAME,
        )

    def _exhibit_expectation(
        self, *, experiment_id: str, state: dict[str, Any]
    ) -> dict[str, object]:
        path = self._exhibit_path(experiment_id=experiment_id, state=state)
        return {
            "final_path": path,
            "preview_tool": "experiment.exhibit",
            "notice": (
                "At submit_results the system generates a metrics exhibit from "
                "up to the newest 50 MLflow runs in this attempt's window (no "
                "curation; the cap is recorded) and eligible pinned result JSON "
                "(metrics.json, results.json, and results/*.json associated with "
                "role 'result'). It pins the exhibit when matching runs are found, "
                "or when MLflow is unavailable after a plugin-created run, at "
                f"{path}. When pinned, your report must reference "
                f"{METRICS_EXHIBIT_FILENAME} and answer around it — log every run "
                "to the MLflow env you were handed, tag project_id/experiment_id, "
                "and pull result files before submitting. Preview anytime with "
                "experiment.exhibit; later runs remain in MLflow but are outside "
                "the finalized exhibit."
            ),
        }


def _persisted_run(run: dict[str, Any]) -> PersistedRunState:
    persisted = {key: run[key] for key in _RUN_FIELDS if key in run}
    if "created" in run:
        persisted["created_by_plugin"] = bool(run["created"])
    return cast(PersistedRunState, persisted)


__all__ = ["TransitionExperiment", "TransitionResponse"]

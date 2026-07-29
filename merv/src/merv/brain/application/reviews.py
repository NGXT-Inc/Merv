"""Producer-facing review queries and their event-keyed response reactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from merv.shared.artifact_roles import EXHIBIT_ROLE, GATED_ROLES

from ..artifacts import Artifacts
from ..kernel.utils import parse_iso
from ..research_core import Research
from .events import EventDispatcher
from .experiments.context import ExperimentContextQuery
from .project_context import ProjectContextQuery
from .review_handoff import reviewer_handoff_payload
from .reflections import ReflectionCommands


@dataclass(kw_only=True, eq=False, repr=False)
class RequestReview:
    """Add delivery instructions to a Research-owned review capability."""

    research: Research

    def execute(
        self,
        *,
        target_type: str,
        target_id: str,
        role: str,
        reason: str = "",
        producer_session_id: str = "main",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        result = self.research.request_review(
            target_type=target_type,
            target_id=target_id,
            role=role,
            reason=reason,
            producer_session_id=producer_session_id,
            project_id=project_id,
        )
        return {
            **result,
            "reviewer_handoff": reviewer_handoff_payload(
                role=role,
                target_type=target_type,
                target_id=target_id,
                review_request_id=str(result["review_request_id"]),
                reviewer_capability=str(result["reviewer_capability"]),
            ),
        }


@dataclass(kw_only=True, eq=False, repr=False)
class StartReviewSession:
    """Start a pinned review, then attach bounded orientation for its target."""

    research: Research
    artifacts: Artifacts
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
            self.research.start_review(
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
        submitted_artifacts = _submitted_artifacts(
            artifacts=self.artifacts,
            snapshot=target_snapshot,
        )
        result["read_scope"] = [
            "claim",
            "experiment",
            "reflection",
            "artifact",
            "review",
        ]
        result["project_context"] = self.project_context.build(
            project_id=project_id
        )
        if target_type == "experiment":
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
            result["submitted_artifacts"] = submitted_artifacts
            result["reflection_context"] = self.reflections.get(
                project_id=project_id,
                reflection_id=target_id,
            )
        return result


@dataclass(kw_only=True, eq=False, repr=False)
class ReadReviewStatus:
    """Read canonical review state, then attach best-effort producer guidance."""

    research: Research
    dispatcher: EventDispatcher

    def execute(
        self, *, target_type: str, target_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        result = present_review_recovery(
            self.research.review_status(
                target_type=target_type,
                target_id=target_id,
                project_id=project_id,
            )
        )
        if target_type != "experiment" or not result.get("reviews"):
            return result
        try:
            state = self.research.experiment_state(
                experiment_id=target_id, project_id=project_id
            )
            event = self.research.latest_submitted_review_event(
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


@dataclass(kw_only=True, slots=True)
class ReviewQueue:
    research: Research

    def __call__(
        self, *, project_id: str | None = None
    ) -> dict[str, Any]:
        return present_review_recovery(
            self.research.review_queue(project_id=project_id)
        )


def present_review_recovery(result: dict[str, Any]) -> dict[str, Any]:
    presented = dict(result)
    presented["requests"] = [
        {**request, "recovery": _recovery(request)}
        for request in result.get("requests", [])
    ]
    return presented


def _recovery(request: dict[str, Any]) -> dict[str, Any]:
    status = str(request.get("status") or "")
    expires = parse_iso(str(request.get("expires_at") or ""))
    expired = expires is None or datetime.now(UTC) > expires
    can_refresh = status in {"requested", "started"}
    recovery: dict[str, Any] = {
        "capability_returned_once": True,
        "capability_available": False,
        "expired": expired,
        "can_request_fresh_capability": can_refresh,
        "reason": (
            "capability lost or expired; request a fresh reviewer capability "
            "for the same target and role (this revokes the open request — "
            "the old capability can no longer start or submit)"
            if can_refresh
            else "review request is closed; inspect submitted reviews instead"
        ),
    }
    if can_refresh:
        recovery["tool"] = "review.request"
        recovery["arguments"] = {
            "target_type": request.get("target_type"),
            "target_id": request.get("target_id"),
            "role": request.get("role"),
        }
    return recovery


def _submitted_artifacts(
    *, artifacts: Artifacts, snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """Hydrate exactly the immutable artifact ids pinned by Research."""
    visible = tuple(
        str(resource.get("artifact_id") or "")
        for resource in snapshot.get("artifacts", [])
        if str(resource.get("role") or "") in GATED_ROLES
        or resource.get("role") == EXHIBIT_ROLE
    )
    found = artifacts.get(artifact_ids=visible, include="content")
    result: list[dict[str, Any]] = []
    for artifact in sorted(found, key=lambda item: item.order):
        if artifact.status != "complete":
            continue
        content = (
            None
            if artifact.data is None
            else artifact.data.decode("utf-8", errors="replace")
        )
        entry: dict[str, Any] = {
            "role": artifact.role,
            "lens_id": artifact.lens_id,
            "path": artifact.path,
            "artifact_id": artifact.id,
            "submission_id": artifact.submission_id,
            "submitted_at": artifact.updated_at or artifact.created_at,
            "content": content,
        }
        if content is None:
            entry["note"] = (
                "submitted content unavailable; ask the producer to "
                "resubmit it with artifact.submit"
            )
        result.append(entry)
    return result


__all__ = [
    "ReadReviewStatus",
    "RequestReview",
    "ReviewQueue",
    "StartReviewSession",
]

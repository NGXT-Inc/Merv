"""Reviewer handoff presentation at the application boundary."""

from __future__ import annotations

from typing import Any

from ..research_core import EXPERIMENT_WORKFLOW, REFLECTION_WORKFLOW


def reviewer_handoff_payload(
    *,
    role: str,
    target_type: str,
    target_id: str,
    review_request_id: str = "",
    reviewer_capability: str = "",
) -> dict[str, Any]:
    workflow = (
        REFLECTION_WORKFLOW
        if target_type == "reflection"
        else EXPERIMENT_WORKFLOW
        if target_type == "experiment"
        else None
    )
    review = None if workflow is None else workflow.review(role)
    skill = "" if review is None else review.skill
    handoff: dict[str, Any] = {
        "role": role,
        "skill": skill,
        "target_type": target_type,
        "target_id": target_id,
        "read_only": True,
        "start_tool": "review.start",
        "submit_tool": "review.submit",
    }
    if review_request_id and reviewer_capability and skill:
        handoff["spawn_prompt"] = (
            f"You are the {role} for {target_type} {target_id}. "
            f"Follow the {skill} skill. Begin by calling review.start with "
            f"review_request_id={review_request_id}, "
            f"reviewer_capability={reviewer_capability}, and your own "
            "session identity as caller_session_id (required; never the "
            "producer's). You are read-only: your sole permitted mutation "
            "is review.submit."
        )
    return handoff

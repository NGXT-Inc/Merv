"""Application-owned catalog of agent actions, tools, and reviewer skills."""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple


class RequirementGuidance(NamedTuple):
    action: str
    allowed: tuple[str, ...]
    artifact_key: str


class ReadyGuidance(NamedTuple):
    gate: str
    action: str
    allowed: tuple[str, ...]


class ReviewGuidance(NamedTuple):
    skill: str
    action_name: str
    pass_action: str


EXPERIMENT_REQUIREMENTS = {
    "plan": RequirementGuidance(
        "write_and_submit_plan", ("artifact.submit",), "plan"
    ),
    "result": RequirementGuidance(
        "run_experiment_and_retain_results",
        (
            "sandbox.request",
            "sandbox.attach",
            "sandbox.terminal",
            "sandbox.get",
            "experiment.transition",
            "artifact.submit",
        ),
        "result",
    ),
    "report": RequirementGuidance(
        "write_and_submit_results_report", ("artifact.submit",), "report"
    ),
    "graph": RequirementGuidance(
        "write_and_submit_logic_graph", ("artifact.submit",), "graph"
    ),
}

EXPERIMENT_READY = {
    "submit_design": ReadyGuidance(
        "design_review_required", "submit_design_for_review", ("experiment.transition",)
    ),
    "start_running": ReadyGuidance(
        "execution_ready",
        "start_running",
        ("sandbox.request", "sandbox.attach", "experiment.transition"),
    ),
    "submit_results": ReadyGuidance(
        "experiment_review_required",
        (
            "submit_results_for_review (call only once the experiment is fully "
            "complete and every success criterion in the experiment intent is "
            "satisfied; do NOT call if the experiment should continue running; "
            "continue with sandbox.* and artifact.submit calls instead and only "
            "transition once the work is truly done; if revision_context is "
            "present, the last review rejected this attempt or an infrastructure "
            "retry was requested — address it before resubmitting)"
        ),
        ("experiment.transition",),
    ),
}

REFLECTION_REQUIREMENTS = {
    "reflection_lens_doc": RequirementGuidance(
        "fan_out_reflection_subagents", ("artifact.submit",), "reflection"
    ),
    "project_graph": RequirementGuidance(
        "update_and_submit_project_graph", ("artifact.submit",), "project_graph"
    ),
    "reflection_doc": RequirementGuidance(
        "write_and_submit_reflection_doc", ("artifact.submit",), "reflection_doc"
    ),
    "change_spec": RequirementGuidance(
        "write_and_submit_change_spec", ("artifact.submit",), "change_spec"
    ),
}

REFLECTION_READY = {
    "submit_reflections": ReadyGuidance(
        "reflections_complete", "submit_reflections", ("reflection.transition",)
    ),
    "submit_reflection_artifacts": ReadyGuidance(
        "reflection_review_required",
        (
            "submit_reflection_artifacts (call only once the project graph "
            "reflects the reconciled reasoning state, the reflection doc explains "
            "the scientific argument concisely, and the change spec represents "
            "the intended belief-state update; if revision_context is present, "
            "the last review rejected this attempt — address it before "
            "resubmitting)"
        ),
        ("reflection.transition",),
    ),
}

REVIEWS = {
    "design_reviewer": ReviewGuidance(
        "experiment-design-review", "design_review", "mark_ready_to_run"
    ),
    "experiment_reviewer": ReviewGuidance(
        "experiment-attempt-review", "experiment_review", "complete_experiment"
    ),
    "reflection_reviewer": ReviewGuidance(
        "project-reflection-review", "reflection_review", "publish_reflection"
    ),
}


# A launched reviewer is already at work, so only these states want another one.
REVIEW_LAUNCH_STATUSES = frozenset({"none", "attested_blocked", "requested"})


def review_presentation_status(
    *, satisfied: bool, status: str, problems: Sequence[str] = ()
) -> str:
    """Name a review gate's state the way agents see it.

    Takes the review gate's own facts — the same ones the checklist item and
    the RequirementEvaluation carry — so every surface names the state once.
    A live request wins over an attested-but-blocked pass, which is what
    'wait for the reviewer you already have' depends on.
    """

    if satisfied:
        return "passed"
    if status in {"requested", "started"}:
        return status
    return "attested_blocked" if problems else "none"


def review_action(review: ReviewGuidance, *, review_status: str) -> str:
    """The single agent action a review gate implies, for every surface."""

    if review_status == "passed":
        return review.pass_action
    if review_status in REVIEW_LAUNCH_STATUSES:
        return f"launch_{review.action_name}er"
    return f"wait_for_{review.action_name}"


__all__ = [
    "EXPERIMENT_READY",
    "EXPERIMENT_REQUIREMENTS",
    "REFLECTION_READY",
    "REFLECTION_REQUIREMENTS",
    "REVIEWS",
    "REVIEW_LAUNCH_STATUSES",
    "ReadyGuidance",
    "RequirementGuidance",
    "ReviewGuidance",
    "review_action",
    "review_presentation_status",
]

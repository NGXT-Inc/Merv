"""Declarative review rejection return routing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewReturnRule:
    allowed: tuple[str, ...]
    default: str
    invalid_message: str
    explicit_required: bool = False
    required_message: str = ""
    forbidden: tuple[tuple[str, str], ...] = ()


PASS_RETURN_TO_ERROR = (
    "return_to only applies when the verdict is needs_changes or fail"
)

EXPERIMENT_RETURN_TO_ERROR = "return_to must be 'planned' or 'running'"
SYNTHESIS_RETURN_TO_ERROR = (
    "return_to must be 'reflecting' or 'synthesizing' for reflection reviews"
)

EXPERIMENT_REVIEWER_RETURN_TO_REQUIRED = (
    "experiment-attempt-review rejections must set return_to: 'planned' if the "
    "results show the plan itself is flawed, or 'running' if the plan "
    "stands but execution or the conclusion is flawed"
)
DESIGN_REVIEW_RUNNING_ERROR = (
    "experiment-design-review rejections cannot return_to 'running'; a flawed plan "
    "goes back to 'planned'"
)
REFLECTION_REVIEWER_RETURN_TO_REQUIRED = (
    "project-reflection-review rejections must set return_to: 'reflecting' "
    "to re-launch the reflection fan-out (the reflections "
    "themselves are inadequate), or 'synthesizing' if the "
    "reflections stand but the reflection artifacts must be revised"
)

REVIEW_RETURN_RULES: dict[tuple[str, str], ReviewReturnRule] = {
    ("experiment", "*"): ReviewReturnRule(
        allowed=("", "planned", "running"),
        default="planned",
        invalid_message=EXPERIMENT_RETURN_TO_ERROR,
    ),
    ("experiment", "experiment_reviewer"): ReviewReturnRule(
        allowed=("", "planned", "running"),
        default="planned",
        invalid_message=EXPERIMENT_RETURN_TO_ERROR,
        explicit_required=True,
        required_message=EXPERIMENT_REVIEWER_RETURN_TO_REQUIRED,
    ),
    ("experiment", "design_reviewer"): ReviewReturnRule(
        allowed=("", "planned", "running"),
        default="planned",
        invalid_message=EXPERIMENT_RETURN_TO_ERROR,
        forbidden=(("running", DESIGN_REVIEW_RUNNING_ERROR),),
    ),
    ("synthesis", "*"): ReviewReturnRule(
        allowed=("", "reflecting", "synthesizing"),
        default="synthesizing",
        invalid_message=SYNTHESIS_RETURN_TO_ERROR,
    ),
    ("synthesis", "reflection_reviewer"): ReviewReturnRule(
        allowed=("", "reflecting", "synthesizing"),
        default="synthesizing",
        invalid_message=SYNTHESIS_RETURN_TO_ERROR,
        explicit_required=True,
        required_message=REFLECTION_REVIEWER_RETURN_TO_REQUIRED,
    ),
}


def resolve_review_return(
    *, target_type: str, role: str, verdict: str, return_to: str
) -> str:
    """Resolve a submitted review return target or raise ``ValueError``."""
    value = (return_to or "").strip()
    rule = REVIEW_RETURN_RULES.get((target_type, role)) or REVIEW_RETURN_RULES.get(
        (target_type, "*")
    )
    if rule is None:
        raise ValueError(f"unknown review target type: {target_type}")
    if value not in rule.allowed:
        raise ValueError(rule.invalid_message)
    if verdict == "pass":
        if value:
            raise ValueError(PASS_RETURN_TO_ERROR)
        return ""
    for forbidden, message in rule.forbidden:
        if value == forbidden:
            raise ValueError(message)
    if rule.explicit_required and not value:
        raise ValueError(rule.required_message)
    return value or rule.default

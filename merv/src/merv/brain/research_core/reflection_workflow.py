# If you update this file, you must consult research_core.md to see whether research_core.md needs to be updated. research_core.md must not exceed 100 lines.
"""The complete project-reflection lifecycle and its start policy."""

from __future__ import annotations

from merv.shared.artifact_roles import PROJECT_GRAPH_ROLE, REFLECTION_LENS_DOC_ROLE

from .workflow_schema import (
    ArtifactNeed,
    ReviewGate,
    ReviewReturn,
    State,
    Transition,
    Workflow,
    validate_workflow,
)


REFLECTION_IDLE_RECOMMEND_NEW_TERMINAL_THRESHOLD = 1
REFLECTION_NUDGE_NEW_TERMINAL_THRESHOLD = 3
REFLECTION_BLOCK_NEW_TERMINAL_THRESHOLD = 5

ROSTER_SIZE = 5
CORE_LENSES: tuple[dict[str, str], ...] = (
    {
        "id": "amplify",
        "title": "Amplify what works",
        "charter": (
            "What worked, and what should we do more of? Identify positive "
            "signal, repeated wins, promising mechanisms, and directions where "
            "additional investment is justified."
        ),
    },
    {
        "id": "avoid",
        "title": "Avoid what failed",
        "charter": (
            "What did not work, and what should we avoid? Build the "
            "negative-knowledge ledger from dead_end graph nodes, abandoned "
            "attempts and experiments, and needs_changes review histories: "
            "direction tested, setting, what happened, why it failed."
        ),
    },
    {
        "id": "entropy",
        "title": "Entropy & weird bets",
        "charter": (
            "What unlikely, high-variance things should we try to escape the "
            "project's current local optimum? Generate strange but testable "
            "ideas, surprising pivots, and experiments the other lenses would "
            "probably dismiss too quickly."
        ),
    },
)
CORE_LENS_IDS = tuple(lens["id"] for lens in CORE_LENSES)

RETURN_TO_REFLECTING = ReviewReturn(
    to_status="reflecting",
    attempt="new",
    event_type="reflection.returned_to_reflecting",
    choose_when=(
        "The lens reflections or their coverage are inadequate and the "
        "complete fan-out must run again."
    ),
    revision=(
        "Sent back to reflecting: re-launch the reflection fan-out — every "
        "roster lens must submit a fresh reflection for the new attempt"
    ),
)
RETURN_TO_SYNTHESIZING = ReviewReturn(
    to_status="synthesizing",
    attempt="same",
    event_type="reflection.returned_to_synthesizing",
    default=True,
    choose_when=(
        "The lens reflections stand, but the project graph, reflection "
        "document, or change spec needs revision."
    ),
    revision=(
        "Sent back to synthesizing: the reflections stand; revise the "
        "reflection artifacts (project graph, reflection doc, and/or change "
        "spec) and resubmit"
    ),
)

REFLECTION_RETURN_ERROR = (
    "return_to must be 'reflecting' or 'synthesizing' for reflection reviews"
)


REFLECTION_WORKFLOW = Workflow(
    target_type="reflection",
    subject="reflection wave",
    initial="reflecting",
    success_status="published",
    event_type="reflection.transitioned",
    review_return_error=REFLECTION_RETURN_ERROR,
    states=(
        State(
            name="reflecting",
            requirements=(
                ArtifactNeed(
                    role=REFLECTION_LENS_DOC_ROLE,
                    error=(
                        "no reflections are submitted yet: fan out one "
                        "read-only subagent per roster lens; each subagent "
                        "writes its reflection (e.g. "
                        "reflections/<syn_id>/reflections/<lens_id>.md) and "
                        "submits it with artifact.submit (role "
                        "'reflection_lens_doc', lens_id=<lens_id>) for this "
                        "reflection wave; every lens document needs a "
                        "non-empty Summary section"
                    ),
                    validator="roster",
                    gate="reflection_roster_incomplete",
                    missing=(
                        "one reflection document with a non-empty Summary per "
                        "roster lens (role 'reflection_lens_doc')"
                    ),
                    label="Per-lens reflections submitted",
                    action="fan_out_reflection_subagents",
                    tools=("artifact.submit",),
                    artifact_key="reflection",
                ),
            ),
            forward=Transition(
                name="submit_reflections",
                to_status="synthesizing",
                requires_prose=(
                    "every roster lens must have its own reflection submitted "
                    "to this reflection wave (role 'reflection_lens_doc', with "
                    "its lens_id) for the current attempt, with a non-empty "
                    "Summary section for macro views — each reflection "
                    "document is authored and submitted by its own subagent"
                ),
                gate="reflections_complete",
                action="submit_reflections",
                tools=("reflection.transition",),
            ),
        ),
        State(
            name="synthesizing",
            requirements=(
                ArtifactNeed(
                    role=PROJECT_GRAPH_ROLE,
                    error=(
                        "the project logic graph must be submitted before "
                        "reflection review: update the living project graph "
                        "(e.g. project/logic_graph.json — the current logic "
                        "state of the whole project as a DAG of at most 16 "
                        "nodes) and submit it with artifact.submit (role "
                        "'project_graph') — see "
                        "skills/research-workflow/graph-template.md"
                    ),
                    validator="graph",
                    gate="project_graph_required",
                    missing="project logic graph artifact (role 'project_graph')",
                    label="Project graph present and valid",
                    action="update_and_submit_project_graph",
                    tools=("artifact.submit",),
                    artifact_key="project_graph",
                ),
                ArtifactNeed(
                    role="reflection_doc",
                    error=(
                        "a concise reflection document must be submitted "
                        "before reflection review: write the main agent's short "
                        "markdown reflection on the five lens reflections and "
                        "submit it with artifact.submit (role "
                        "'reflection_doc') — see "
                        "skills/project-reflection/"
                        "reflection-artifacts-template.md"
                    ),
                    validator="reflection_doc",
                    gate="reflection_doc_required",
                    missing="reflection document artifact (role 'reflection_doc')",
                    label="Reflection document present and valid",
                    action="write_and_submit_reflection_doc",
                    tools=("artifact.submit",),
                    artifact_key="reflection_doc",
                ),
                ArtifactNeed(
                    role="change_spec",
                    error=(
                        "a change spec must be submitted before reflection "
                        "review: write JSON with claim_changes plus a "
                        "create_experiments decision (1-3 experiments) and "
                        "submit it with artifact.submit (role 'change_spec') — "
                        "see skills/project-reflection/"
                        "reflection-artifacts-template.md"
                    ),
                    validator="change_spec",
                    gate="change_spec_required",
                    missing="change spec artifact (role 'change_spec')",
                    label="Change spec present and materializable",
                    action="write_and_submit_change_spec",
                    tools=("artifact.submit",),
                    artifact_key="change_spec",
                ),
            ),
            forward=Transition(
                name="submit_reflection_artifacts",
                to_status="reflection_review",
                requires_prose=(
                    "the updated project logic graph (role 'project_graph', "
                    "valid JSON DAG of at most 16 nodes), a concise reflection "
                    "document (role 'reflection_doc'), AND a machine-actionable "
                    "change spec (role 'change_spec') must be submitted to this "
                    "reflection wave for the current attempt; after approval, "
                    "publish applies the claim changes and creates the next "
                    "experiment wave"
                ),
                gate="reflection_review_required",
                action=(
                    "submit_reflection_artifacts (call only once the project "
                    "graph reflects the reconciled reasoning state, the "
                    "reflection doc explains the scientific argument "
                    "concisely, and the change spec represents the intended "
                    "belief-state update; if revision_context is present, the "
                    "last review rejected this attempt — address it before "
                    "resubmitting)"
                ),
                tools=("reflection.transition",),
            ),
        ),
        State(
            name="reflection_review",
            review=ReviewGate(
                role="reflection_reviewer",
                error="reflection review must pass before publish",
                blocker_code="reflection_review_required",
                label="Reflection review passed",
                skill="project-reflection-review",
                pass_action="publish_reflection",
                returns=(RETURN_TO_REFLECTING, RETURN_TO_SYNTHESIZING),
                return_choice_required=True,
                return_required_error=(
                    "project-reflection-review rejections must set return_to: "
                    "'reflecting' to re-launch the reflection fan-out (the "
                    "reflections themselves are inadequate), or "
                    "'synthesizing' if the reflections stand but the "
                    "reflection artifacts must be revised"
                ),
            ),
            forward=Transition(
                name="publish",
                to_status="published",
                requires_prose="a passing reflection_reviewer review",
                action="publish_reflection",
                tools=("reflection.transition",),
                effects=("materialize_change_spec", "pin_project_graph"),
            ),
        ),
    ),
    global_exits=(
        Transition(
            name="abandon",
            to_status="abandoned",
            action="abandon_reflection",
            tools=("reflection.transition",),
        ),
    ),
)

validate_workflow(REFLECTION_WORKFLOW)

REFLECTION_TERMINAL_STATUSES = REFLECTION_WORKFLOW.terminal_statuses
REFLECTION_TRANSITION_VALUES = REFLECTION_WORKFLOW.transition_names


__all__ = [
    "CORE_LENSES",
    "CORE_LENS_IDS",
    "REFLECTION_BLOCK_NEW_TERMINAL_THRESHOLD",
    "REFLECTION_IDLE_RECOMMEND_NEW_TERMINAL_THRESHOLD",
    "REFLECTION_NUDGE_NEW_TERMINAL_THRESHOLD",
    "REFLECTION_TERMINAL_STATUSES",
    "REFLECTION_TRANSITION_VALUES",
    "REFLECTION_WORKFLOW",
    "ROSTER_SIZE",
]

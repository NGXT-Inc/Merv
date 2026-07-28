from __future__ import annotations

import unittest
from dataclasses import fields

from merv.brain.application.gate_checklist import present_gate_checklist
from merv.brain.application.guidance_catalog import REVIEWS
from merv.brain.application.status_guidance import StatusGuidancePolicy
from merv.brain.research_core.domain.gates import (
    ForwardTransition,
    ReviewRequirement,
    RoleRequirement,
)
from merv.brain.research_core.domain.reflection_gates import REFLECTION_GATE_TABLE
from merv.brain.research_core.domain.workflow_gates import GATE_TABLE
from merv.brain.research_core.facade import GateEvaluation, RequirementEvaluation


class GateChecklistPresentationTest(unittest.TestCase):
    def test_research_contract_contains_no_agent_guidance_metadata(self) -> None:
        self.assertEqual(
            {field.name for field in fields(RoleRequirement)},
            {"role", "error", "validator", "gate", "missing", "label"},
        )
        self.assertEqual(
            {field.name for field in fields(ReviewRequirement)},
            {"role", "error", "blocker_code", "label"},
        )
        self.assertEqual(
            {field.name for field in fields(ForwardTransition)},
            {
                "name",
                "to_status",
                "requires_prose",
                "requirements",
                "review",
            },
        )

    def test_resource_item_restores_exact_agent_fields_and_order(self) -> None:
        checklist = {
            "status": "planned",
            "transition": "submit_design",
            "leads_to": "design_review",
            "ready": False,
            "items": [
                {
                    "id": "resource:plan",
                    "kind": "resource",
                    "role": "plan",
                    "label": "Plan associated and valid",
                    "satisfied": False,
                    "status": "missing",
                    "gate": "plan_required",
                    "validator": "plan",
                    "missing": "experiment plan resource",
                }
            ],
        }

        item = present_gate_checklist(checklist)["items"][0]

        self.assertEqual(
            list(item),
            [
                "id",
                "kind",
                "role",
                "label",
                "satisfied",
                "status",
                "gate",
                "action",
                "validator",
                "missing",
            ],
        )
        self.assertEqual(item["action"], "write_and_submit_plan")

    def test_review_item_uses_application_owned_skill_and_pass_action(self) -> None:
        base = {
            "id": "review:design_reviewer",
            "kind": "review",
            "role": "design_reviewer",
            "label": "Design review passed",
            "satisfied": True,
            "status": "passed",
            "gate": "design_review",
        }

        item = present_gate_checklist(
            {"items": [base]}
        )["items"][0]

        self.assertEqual(item["action"], "mark_ready_to_run")
        self.assertEqual(item["skill"], "experiment-design-review")
        self.assertEqual(list(item)[-2:], ["action", "skill"])

    def test_reflection_lens_keeps_lens_id_before_label_and_inserts_action_after_gate(self) -> None:
        item = present_gate_checklist(
            {
                "items": [
                    {
                        "id": "reflection_lens:amplify",
                        "kind": "reflection_lens",
                        "role": "reflection_lens_doc",
                        "lens_id": "amplify",
                        "label": "Amplify reflection submitted",
                        "satisfied": False,
                        "status": "missing",
                        "gate": "reflection_roster_incomplete",
                        "missing": "reflection doc for lens 'amplify'",
                    }
                ]
            }
        )["items"][0]

        self.assertEqual(
            list(item),
            [
                "id",
                "kind",
                "role",
                "lens_id",
                "label",
                "satisfied",
                "status",
                "gate",
                "action",
                "missing",
            ],
        )
        self.assertEqual(item["action"], "fan_out_reflection_subagents")


def _blocked_reason(role: str) -> str:
    """The verified-review blocker verbatim from evaluate_review_gate."""

    return (
        f"a {role} review passed but its independence is only attested "
        "(the reviewer did not present a session identity) and this project "
        "requires verified reviews (require_verified_reviews is on): request "
        "a fresh review and have the reviewer pass its own caller_session_id "
        "to review.start"
    )


# The unsatisfied review-gate facts evaluate_review_gate can emit, and the one
# action both consumers owe an agent for each. A reviewer that has already
# started is the only state that must NOT tell the agent to launch another —
# a live request outranks an attested-but-blocked earlier pass, so the last
# two rows carry both at once.
_UNSATISFIED_REVIEW_FACTS = (
    ("pending", False, "launch"),
    ("pending", True, "launch"),
    ("requested", False, "launch"),
    ("started", False, "wait"),
    ("requested", True, "launch"),
    ("started", True, "wait"),
)
_REVIEW_TARGET_TYPES = {
    "design_reviewer": "experiment",
    "experiment_reviewer": "experiment",
    "reflection_reviewer": "reflection",
}
# role -> (target type, the status the gate is evaluated from, its forward
# transition, its requirement) — read off the real gate tables so the fixture
# can't drift from the contract evaluate_review_gate reads.
_REVIEW_GATES = {
    transition.review.role: (
        _REVIEW_TARGET_TYPES[transition.review.role],
        status,
        transition.name,
        transition.review,
    )
    for table in (GATE_TABLE, REFLECTION_GATE_TABLE)
    for status, transition in table.items()
    if transition.review is not None
}


def _review_item(*, role: str, status: str, problems: tuple[str, ...]) -> dict:
    """The exact checklist item evaluate_review_gate builds for a review gate."""

    _, gate, _, requirement = _REVIEW_GATES[role]
    item: dict = {
        "id": f"review:{role}",
        "kind": "review",
        "role": role,
        "label": requirement.label,
        "satisfied": status == "passed",
        "status": status,
        "gate": gate,
    }
    if problems:
        item["problems"] = list(problems)
    if status in {"requested", "started"}:
        item.update(request_id="rr_1", expires_at="2026-07-21T18:00:00Z")
    return item


class ReviewActionParityTest(unittest.TestCase):
    """The checklist and workflow.status_and_next name one action per gate.

    Both derive it from the same REVIEWS catalog, so an agent reading
    experiment.get's gate_checklist and one reading the status payload must
    never be told different things about the same unsatisfied review gate.
    """

    def setUp(self) -> None:
        self.policy = StatusGuidancePolicy()

    def _status_action(
        self,
        *,
        role: str,
        item: dict,
        status: str,
        problems: tuple[str, ...],
        satisfied: bool = False,
    ) -> str:
        target_type, target_status, transition, requirement = _REVIEW_GATES[role]
        review = RequirementEvaluation(
            role,
            status,
            "" if satisfied else requirement.blocker_code,
            # evaluate_review_gate raises the blocker over the generic error.
            "" if satisfied else problems[0] if problems else requirement.error,
            problems,
            (item,),
        )
        evaluation = GateEvaluation(
            subject=target_type,
            status=target_status,
            transition=transition,
            leads_to=None,
            terminal=False,
            requirements=(),
            review=review,
            legal_transitions=(),
        )
        target = {"id": "tgt_1", "status": target_status, "name": "example"}
        if target_type == "reflection":
            return self.policy.project_reflection(
                open_wave=target,
                evaluation=evaluation,
                signal={},
                idle=False,
            )["workflow"]["next_action"]
        return self.policy.experiment(
            experiment=target, sandboxes=[], evaluation=evaluation
        )["next_action"]

    def test_unsatisfied_review_gate_agrees_across_both_consumers(self) -> None:
        for role, guidance in REVIEWS.items():
            for status, blocked, kind in _UNSATISFIED_REVIEW_FACTS:
                with self.subTest(role=role, status=status, blocked=blocked):
                    problems = (_blocked_reason(role),) if blocked else ()
                    item = _review_item(role=role, status=status, problems=problems)
                    expected = (
                        f"launch_{guidance.action_name}er"
                        if kind == "launch"
                        else f"wait_for_{guidance.action_name}"
                    )
                    checklist_action = present_gate_checklist({"items": [dict(item)]})[
                        "items"
                    ][0]["action"]
                    status_action = self._status_action(
                        role=role, item=item, status=status, problems=problems
                    )
                    self.assertEqual(checklist_action, expected)
                    self.assertEqual(status_action, expected)

    def test_started_review_never_tells_an_agent_to_launch_a_second_reviewer(
        self,
    ) -> None:
        item = _review_item(role="design_reviewer", status="started", problems=())

        self.assertEqual(
            present_gate_checklist({"items": [dict(item)]})["items"][0]["action"],
            "wait_for_design_review",
        )
        self.assertEqual(
            self._status_action(
                role="design_reviewer", item=item, status="started", problems=()
            ),
            "wait_for_design_review",
        )

    def test_passed_review_gate_agrees_across_both_consumers(self) -> None:
        for role, guidance in REVIEWS.items():
            with self.subTest(role=role):
                item = _review_item(role=role, status="passed", problems=())

                self.assertEqual(
                    present_gate_checklist({"items": [dict(item)]})["items"][0]["action"],
                    guidance.pass_action,
                )
                self.assertEqual(
                    self._status_action(
                        role=role,
                        item=item,
                        status="passed",
                        problems=(),
                        satisfied=True,
                    ),
                    guidance.pass_action,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

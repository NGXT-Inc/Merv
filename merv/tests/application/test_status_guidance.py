from __future__ import annotations

import unittest

from merv.brain.application.status_guidance import (
    StatusGuidancePolicy,
)
from merv.brain.research_core import (
    EXPERIMENT_WORKFLOW,
    REFLECTION_WORKFLOW,
)
from merv.brain.research_core.policy import GateEvaluation, RequirementEvaluation


def _requirement(
    role: str,
    status: str,
    blocker: str,
    *,
    problems: tuple[str, ...] = (),
    items: tuple[dict[str, object], ...] = (),
) -> RequirementEvaluation:
    error = problems[0] if problems else f"{role} required"
    return RequirementEvaluation(role, status, blocker, error, problems, items)


def _evaluation(
    *,
    subject: str = "experiment",
    status: str,
    requirements: tuple[RequirementEvaluation, ...] = (),
    review: RequirementEvaluation | None = None,
) -> GateEvaluation:
    return GateEvaluation(
        workflow=(
            REFLECTION_WORKFLOW
            if subject == "reflection wave"
            else EXPERIMENT_WORKFLOW
        ),
        status=status,
        requirements=requirements,
        review=review,
    )


class StatusGuidanceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = StatusGuidancePolicy()

    def test_workflow_entries_carry_their_agent_guidance(self) -> None:
        for workflow in (EXPERIMENT_WORKFLOW, REFLECTION_WORKFLOW):
            for state in workflow.states:
                for requirement in state.requirements:
                    self.assertTrue(requirement.action, requirement.role)
                    self.assertTrue(requirement.tools, requirement.role)
                self.assertTrue(state.forward.action, state.forward.name)
                self.assertTrue(state.forward.tools, state.forward.name)
                if state.review is not None:
                    self.assertTrue(state.review.skill, state.review.role)
                    self.assertTrue(state.review.pass_action, state.review.role)

    def test_missing_requirement_precedes_an_earlier_invalid_one(self) -> None:
        result = self.policy.experiment(
            experiment={"id": "exp_1", "name": "example", "status": "running"},
            sandboxes=[],
            evaluation=_evaluation(
                status="running",
                requirements=(
                    _requirement("result", "present", "", items=({},)),
                    _requirement(
                        "report",
                        "invalid",
                        "report_invalid",
                        problems=("report is invalid",),
                    ),
                    _requirement(
                        "graph",
                        "missing",
                        "logic_graph_required",
                        items=(
                            {
                                "status": "missing",
                                "missing": "logic graph artifact (role 'graph')",
                            },
                        ),
                    ),
                ),
            ),
        )
        self.assertEqual(result["current_gate"], "logic_graph_required")
        self.assertEqual(result["next_action"], "write_and_submit_logic_graph")
        self.assertEqual(
            result["missing_evidence"], ["logic graph artifact (role 'graph')"]
        )

    def test_live_sandbox_changes_only_the_execution_gate_name(self) -> None:
        result = self.policy.experiment(
            experiment={"id": "exp_1", "name": "example", "status": "running"},
            sandboxes=[{"status": "running"}],
            evaluation=_evaluation(
                status="running",
                requirements=(
                    _requirement(
                        "result",
                        "missing",
                        "execution_ready",
                        items=({"status": "missing", "missing": "result resource"},),
                    ),
                ),
            ),
        )
        self.assertEqual(result["current_gate"], "execution_active")
        self.assertEqual(result["next_action"], "run_experiment_and_retain_results")

    def test_review_request_preserves_spawn_guidance_shape(self) -> None:
        result = self.policy.experiment(
            experiment={"id": "exp_1", "status": "design_review"},
            sandboxes=[],
            evaluation=_evaluation(
                status="design_review",
                review=_requirement(
                    "design_reviewer",
                    "requested",
                    "design_review_required",
                    items=(
                        {
                            "request_id": "rr_1",
                            "expires_at": "2026-07-21T18:00:00Z",
                        },
                    ),
                ),
            ),
        )
        self.assertEqual(result["next_action"], "launch_design_reviewer")
        self.assertEqual(
            result["allowed_actions"], ["workflow.status_and_next", "review.request"]
        )
        self.assertEqual(
            result["review_gate"],
            {
                "role": "design_reviewer",
                "skill": "experiment-design-review",
                "target_type": "experiment",
                "target_id": "exp_1",
                "status": "requested",
                "label": "Reviewer pending",
                "read_only": True,
                "request_id": "rr_1",
                "expires_at": "2026-07-21T18:00:00Z",
            },
        )

    def test_reflection_roster_uses_each_factual_missing_lens(self) -> None:
        result = self.policy.project_reflection(
            open_wave={
                "id": "syn_1",
                "status": "reflecting",
                "revision_context": "",
            },
            evaluation=_evaluation(
                subject="reflection wave",
                status="reflecting",
                requirements=(
                    _requirement(
                        "reflection_lens_doc",
                        "missing",
                        "reflection_roster_incomplete",
                        items=(
                            {"status": "missing", "missing": "amplify reflection"},
                            {"status": "missing", "missing": "avoid reflection"},
                        ),
                    ),
                ),
            ),
            signal={"experiment_create_blocked": False},
            idle=True,
        )
        assert result is not None
        self.assertEqual(
            result["workflow"]["missing_evidence"],
            ["amplify reflection", "avoid reflection"],
        )

    def test_idle_reflection_hint_preserves_existing_wording(self) -> None:
        result = self.policy.project_reflection(
            open_wave=None,
            evaluation=None,
            signal={
                "new_terminal_since_publish": 1,
                "contradicted_flip": False,
                "has_new_material": True,
                "stale": False,
                "experiment_create_blocked": False,
                "last_published_reflection_id": None,
                "claims_changed_since_publish": 0,
            },
            idle=True,
        )
        assert result is not None
        self.assertEqual(result["signal"]["hint"], "")
        self.assertEqual(list(result["signal"])[-1], "hint")
        self.assertEqual(
            result["hint"],
            "No experiments are active and 1 experiment has finished and no "
            "project reflection exists yet — a good moment for a project reflection "
            "(reflection.create, project-reflection skill), or start the next "
            "experiment if the logic state is current.",
        )


if __name__ == "__main__":
    unittest.main()

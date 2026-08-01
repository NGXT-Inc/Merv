from __future__ import annotations

import unittest

from merv.brain.research_core import (
    EXPERIMENT_WORKFLOW,
    REFLECTION_WORKFLOW,
)
from merv.brain.research_core.policy import (
    REVIEW_GATE_EXEMPT_ROLES,
    review_snapshot_id,
    snapshot_from_id,
    validate_synopsis,
)
from merv.brain.research_core.workflow_schema import (
    resolve_review_return,
    validate_workflow,
)


class WorkflowSchemaTest(unittest.TestCase):
    def test_declarations_are_complete_and_self_consistent(self) -> None:
        for workflow in (EXPERIMENT_WORKFLOW, REFLECTION_WORKFLOW):
            with self.subTest(workflow=workflow.target_type):
                validate_workflow(workflow)
                self.assertTrue(workflow.state(workflow.initial))
                self.assertTrue(workflow.transitions)
                self.assertEqual(
                    len(workflow.transition_names),
                    len(set(workflow.transition_names)),
                )

    def test_experiment_forward_and_review_return_paths_are_declared(self) -> None:
        self.assertEqual(
            EXPERIMENT_WORKFLOW.forward_path("planned"),
            (
                "planned",
                "design_review",
                "ready_to_run",
                "running",
                "experiment_review",
                "complete",
            ),
        )
        cases = (
            ("design_reviewer", "needs_changes", "", "planned", "new"),
            ("experiment_reviewer", "needs_changes", "planned", "planned", "new"),
            ("experiment_reviewer", "needs_changes", "running", "running", "same"),
        )
        for role, verdict, requested, destination, attempt in cases:
            with self.subTest(role=role, requested=requested):
                route = resolve_review_return(
                    workflow=EXPERIMENT_WORKFLOW,
                    role=role,
                    verdict=verdict,
                    return_to=requested,
                )
                self.assertEqual(
                    (route.to_status, route.attempt), (destination, attempt)
                )

    def test_reflection_forward_and_review_return_paths_are_declared(self) -> None:
        self.assertEqual(
            REFLECTION_WORKFLOW.forward_path("reflecting"),
            (
                "reflecting",
                "synthesizing",
                "reflection_review",
                "consolidating",
                "published",
            ),
        )
        cases = (
            ("synthesizing", "same"),
            ("reflecting", "new"),
        )
        for destination, attempt in cases:
            with self.subTest(destination=destination):
                route = resolve_review_return(
                    workflow=REFLECTION_WORKFLOW,
                    role="reflection_reviewer",
                    verdict="needs_changes",
                    return_to=destination,
                )
                self.assertEqual(route.attempt, attempt)
        consolidation = resolve_review_return(
            workflow=REFLECTION_WORKFLOW,
            role="consolidation_reviewer",
            verdict="needs_changes",
            return_to="consolidating",
        )
        self.assertEqual(
            (consolidation.to_status, consolidation.attempt),
            ("consolidating", "same"),
        )

    def test_invalid_review_returns_are_rejected_by_the_schema(self) -> None:
        cases = (
            (EXPERIMENT_WORKFLOW, "human", "pass", "planned"),
            (EXPERIMENT_WORKFLOW, "design_reviewer", "needs_changes", "running"),
            (EXPERIMENT_WORKFLOW, "experiment_reviewer", "needs_changes", ""),
            (REFLECTION_WORKFLOW, "reflection_reviewer", "needs_changes", ""),
            (
                REFLECTION_WORKFLOW,
                "consolidation_reviewer",
                "needs_changes",
                "reflecting",
            ),
        )
        for workflow, role, verdict, destination in cases:
            with self.subTest(role=role, destination=destination):
                with self.assertRaises(ValueError):
                    resolve_review_return(
                        workflow=workflow,
                        role=role,
                        verdict=verdict,
                        return_to=destination,
                    )

    def test_review_snapshot_is_deterministic_and_round_trips(self) -> None:
        target = {
            "id": "exp_1",
            "status": "running",
            "attempt_index": 3,
            "current_attempt_artifacts": [
                {"id": "art_b", "role": "report", "attempt_index": 3},
                {"id": "art_a", "role": "plan", "attempt_index": 3},
            ],
        }
        snapshot = review_snapshot_id(target_type="experiment", target=target)
        self.assertEqual(
            snapshot,
            "experiment|exp_1|running|3|art_a:plan:3,art_b:report:3",
        )
        self.assertEqual(
            snapshot_from_id(snapshot_id=snapshot)["artifacts"],
            [
                {"artifact_id": "art_a", "role": "plan", "attempt_index": 3},
                {"artifact_id": "art_b", "role": "report", "attempt_index": 3},
            ],
        )
        consolidation = review_snapshot_id(
            target_type="reflection",
            target={
                "id": "ref_1",
                "status": "consolidating",
                "attempt_index": 1,
                "snapshot_token": "cpr_1",
                "code_sha": "a" * 40,
            },
        )
        self.assertEqual(
            snapshot_from_id(snapshot_id=consolidation),
            {
                "target_type": "reflection",
                "target_id": "ref_1",
                "status": "consolidating",
                "attempt_index": 1,
                "artifacts": [],
                "snapshot_token": "cpr_1",
                "code_sha": "a" * 40,
            },
        )

    def test_synopsis_and_review_exemptions_keep_the_security_envelope(self) -> None:
        valid = (
            "The attempt clears its registered threshold, while the remaining "
            "qualification is narrow enough to preserve the stated conclusion."
        )
        self.assertEqual(validate_synopsis(f"  {valid}  "), valid)
        for invalid in ("too short", "x" * 421, valid + "\nextra", valid + " exp_abc"):
            with self.subTest(invalid=invalid[:20]):
                with self.assertRaises(ValueError):
                    validate_synopsis(invalid)
        self.assertEqual(REVIEW_GATE_EXEMPT_ROLES, {"human", "automated_check"})

    def test_surface_choices_are_derived_from_the_workflows(self) -> None:
        from merv.brain.research_core import REVIEW_ROLE_VALUES
        from merv.brain.surface.tools.contracts import (
            ExperimentTransitionInput,
            ReflectionTransitionInput,
            ReviewRequestInput,
            ReviewSubmitInput,
        )

        def choices(model, field: str) -> tuple[str, ...]:
            return tuple(model.model_fields[field].annotation.__args__)

        self.assertEqual(
            choices(ExperimentTransitionInput, "transition"),
            EXPERIMENT_WORKFLOW.transition_names,
        )
        self.assertEqual(
            choices(ReflectionTransitionInput, "transition"),
            REFLECTION_WORKFLOW.transition_names,
        )
        self.assertEqual(choices(ReviewRequestInput, "role"), REVIEW_ROLE_VALUES)
        self.assertEqual(
            choices(ReviewSubmitInput, "return_to"),
            (
                "",
                *EXPERIMENT_WORKFLOW.review_return_statuses,
                *REFLECTION_WORKFLOW.review_return_statuses,
            ),
        )


if __name__ == "__main__":
    unittest.main()

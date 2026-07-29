from __future__ import annotations

import json

from merv.brain.kernel.utils import PermissionDeniedError, ValidationError, WorkflowError
from merv.brain.research_core.policy import (
    REFLECTION_BLOCK_NEW_TERMINAL_THRESHOLD,
)

from .scenarios import (
    LENSES,
    VALID_CHANGE_SPEC,
    VALID_PROJECT_GRAPH,
    VALID_REFLECTION,
    ResearchCase,
)


class ReflectionWorkflowTest(ResearchCase):
    def test_roster_and_single_open_wave_are_enforced(self) -> None:
        with self.assertRaisesRegex(ValidationError, "exactly 5 lenses"):
            self.call(
                "reflection.create",
                project_id=self.project_id,
                lenses=[dict(lens) for lens in LENSES[:4]],
            )
        reflection_id = self.create_reflection()
        with self.assertRaisesRegex(WorkflowError, "already open"):
            self.create_reflection("Second")
        abandoned = self.call(
            "reflection.transition",
            project_id=self.project_id,
            reflection_id=reflection_id,
            transition="abandon",
        )
        self.assertEqual(abandoned["status"], "abandoned")
        self.assertTrue(self.create_reflection("Replacement"))

    def test_lens_and_reconciliation_gates_require_the_declared_evidence(self) -> None:
        reflection_id = self.create_reflection()
        with self.assertRaises(WorkflowError):
            self.call(
                "reflection.transition",
                project_id=self.project_id,
                reflection_id=reflection_id,
                transition="submit_reflections",
            )

        self.submit_lenses(reflection_id)
        synthesizing = self.call(
            "reflection.transition",
            project_id=self.project_id,
            reflection_id=reflection_id,
            transition="submit_reflections",
        )
        self.assertEqual(synthesizing["status"], "synthesizing")

        roles = (
            ("project_graph", "project/logic_graph.json", VALID_PROJECT_GRAPH),
            ("reflection_doc", "project/reflection.md", VALID_REFLECTION),
            ("change_spec", "project/change_spec.json", VALID_CHANGE_SPEC),
        )
        for role, path, body in roles:
            with self.assertRaises(WorkflowError):
                self.call(
                    "reflection.transition",
                    project_id=self.project_id,
                    reflection_id=reflection_id,
                    transition="submit_reflection_artifacts",
                )
            self.submit(
                target_type="reflection",
                target_id=reflection_id,
                role=role,
                path=path,
                body=body,
            )
        reviewing = self.call(
            "reflection.transition",
            project_id=self.project_id,
            reflection_id=reflection_id,
            transition="submit_reflection_artifacts",
        )
        self.assertEqual(reviewing["status"], "reflection_review")
        with self.app.store.connect() as conn:
            sealed = conn.execute(
                """
                SELECT COUNT(*) AS n FROM artifacts
                WHERE target_id = ? AND submission_id <> ''
                """,
                (reflection_id,),
            ).fetchone()["n"]
        self.assertEqual(sealed, len(LENSES) + 3)

    def test_publish_materializes_reviewed_change_spec_atomically(self) -> None:
        existing = self.call(
            "claim.create",
            project_id=self.project_id,
            statement="The schedule effect is local.",
        )
        change_spec = json.loads(VALID_CHANGE_SPEC)
        change_spec["claim_changes"].insert(
            0,
            {
                "op": "update",
                "claim_id": existing["id"],
                "status": "supported",
                "confidence": "high",
                "rationale": "The reflection reconciled the evidence.",
            },
        )
        reflection_id = self.create_reflection()
        self.submit_lenses(reflection_id)
        self.call(
            "reflection.transition",
            project_id=self.project_id,
            reflection_id=reflection_id,
            transition="submit_reflections",
        )
        self.submit_reflection_bundle(
            reflection_id, change_spec=json.dumps(change_spec)
        )
        self.call(
            "reflection.transition",
            project_id=self.project_id,
            reflection_id=reflection_id,
            transition="submit_reflection_artifacts",
        )
        with self.assertRaises(WorkflowError):
            self.call(
                "reflection.transition",
                project_id=self.project_id,
                reflection_id=reflection_id,
                transition="publish",
            )
        self.pass_review(
            target_type="reflection",
            target_id=reflection_id,
            role="reflection_reviewer",
        )
        published = self.call(
            "reflection.transition",
            project_id=self.project_id,
            reflection_id=reflection_id,
            transition="publish",
        )

        self.assertEqual(published["status"], "published")
        claims = self.call("claim.list", project_id=self.project_id)["claims"]
        self.assertEqual(
            {claim["statement"]: claim["status"] for claim in claims},
            {
                "The schedule effect is local.": "supported",
                "The schedule effect transfers.": "active",
            },
        )
        experiments = self.call(
            "experiment.list", project_id=self.project_id
        )["experiments"]
        self.assertEqual(
            [(item["name"], item["status"]) for item in experiments],
            [("transfer-test", "planned")],
        )
        self.assertEqual(len(published["materialized_claims"]), 2)
        self.assertEqual(len(published["materialized_experiments"]), 1)

    def test_review_returns_preserve_or_reset_the_attempt_as_declared(self) -> None:
        same = self.drive_reflection_to_review("Same attempt")
        self.review(
            target_type="reflection",
            target_id=same,
            role="reflection_reviewer",
            verdict="needs_changes",
            return_to="synthesizing",
        )
        returned = self.call(
            "reflection.get",
            project_id=self.project_id,
            reflection_id=same,
        )
        self.assertEqual(
            (returned["status"], returned["attempt_index"]),
            ("synthesizing", 1),
        )

        self.call(
            "reflection.transition",
            project_id=self.project_id,
            reflection_id=same,
            transition="abandon",
        )
        reset = self.drive_reflection_to_review("New attempt")
        self.review(
            target_type="reflection",
            target_id=reset,
            role="reflection_reviewer",
            verdict="needs_changes",
            return_to="reflecting",
        )
        returned = self.call(
            "reflection.get",
            project_id=self.project_id,
            reflection_id=reset,
            include_content=True,
        )
        self.assertEqual(
            (returned["status"], returned["attempt_index"]),
            ("reflecting", 2),
        )
        self.assertEqual(
            set(returned["reflection_coverage"]["missing"]),
            {str(lens["id"]) for lens in LENSES},
        )

    def test_reviewer_snapshot_contains_each_lens_and_rejects_producer(self) -> None:
        reflection_id = self.drive_reflection_to_review()
        request = self.call(
            "review.request",
            project_id=self.project_id,
            target_type="reflection",
            target_id=reflection_id,
            role="reflection_reviewer",
            producer_session_id="producer",
        )
        with self.assertRaises(PermissionDeniedError):
            self.call(
                "review.start",
                review_request_id=request["review_request_id"],
                reviewer_capability=request["reviewer_capability"],
                caller_session_id="producer",
            )
        session = self.call(
            "review.start",
            review_request_id=request["review_request_id"],
            reviewer_capability=request["reviewer_capability"],
            caller_session_id="reviewer",
        )
        lens_docs = [
            item
            for item in session["submitted_artifacts"]
            if item["role"] == "reflection_lens_doc"
        ]
        self.assertEqual(
            {item["lens_id"] for item in lens_docs},
            {str(lens["id"]) for lens in LENSES},
        )

    def test_reflection_signal_blocks_new_work_and_publish_resets_it(self) -> None:
        for index in range(REFLECTION_BLOCK_NEW_TERMINAL_THRESHOLD):
            experiment_id = self.create_experiment(f"finished-{index}")
            self.transition_experiment(experiment_id, "abandon")
        signal = self.app.research_core.reflection_overview(
            project_id=self.project_id
        )["signal"]
        self.assertTrue(signal["experiment_create_blocked"])
        with self.assertRaises(WorkflowError):
            self.create_experiment("blocked")

        reflection_id = self.drive_reflection_to_review()
        self.pass_review(
            target_type="reflection",
            target_id=reflection_id,
            role="reflection_reviewer",
        )
        self.call(
            "reflection.transition",
            project_id=self.project_id,
            reflection_id=reflection_id,
            transition="publish",
        )
        signal = self.app.research_core.reflection_overview(
            project_id=self.project_id
        )["signal"]
        self.assertFalse(signal["experiment_create_blocked"])
        self.assertEqual(signal["new_terminal_since_publish"], 0)


if __name__ == "__main__":
    import unittest

    unittest.main()

"""Focused contract tests for artifact.find id batches and content opt-in."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from merv.brain.kernel.utils import NotFoundError
from merv.brain.surface.tools.contracts import ArtifactFindInput
from tests.support.brain import TestBrain


class ArtifactBatchFindTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.app = TestBrain(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
        )
        self.project_id = self.app.call_tool(
            "project", {"action": "create", "name": "Artifact batch retrieval"}
        )["id"]
        self.experiment_id = self.app.call_tool(
            "experiment.create",
            {
                "project_id": self.project_id,
                "name": "artifact-batch",
                "intent": "Exercise ordered artifact retrieval.",
            },
        )["id"]

    def tearDown(self) -> None:
        self.app.shutdown()
        self.tmp.cleanup()

    def _submit(self, *, role: str, path: str, body: str | bytes) -> str:
        return self.app.submit_artifact(
            project_id=self.project_id,
            target_type="experiment",
            target_id=self.experiment_id,
            role=role,
            path=path,
            body=body,
        )["artifact_id"]

    def test_batch_preserves_order_deduplicates_and_hydrates_on_opt_in(self) -> None:
        plan = self._submit(role="plan", path="plan.md", body="Plan body.")
        report = self._submit(role="report", path="report.md", body="Report body.")

        metadata = self.app.call_tool(
            "artifact.find",
            {
                "project_id": self.project_id,
                "artifact_ids": [report, plan, report],
            },
        )
        hydrated = self.app.call_tool(
            "artifact.find",
            {
                "project_id": self.project_id,
                "artifact_ids": [report, plan],
                "include_content": True,
            },
        )

        self.assertEqual(metadata["count"], 2)
        self.assertEqual(
            [artifact["id"] for artifact in metadata["artifacts"]],
            [report, plan],
        )
        self.assertNotIn("content", metadata["artifacts"][0])
        self.assertEqual(
            [artifact["content"]["content"] for artifact in hydrated["artifacts"]],
            ["Report body.", "Plan body."],
        )

    def test_missing_id_fails_the_batch_atomically(self) -> None:
        existing = self._submit(role="plan", path="plan.md", body="Plan body.")

        with self.assertRaises(NotFoundError) as ctx:
            self.app.call_tool(
                "artifact.find",
                {
                    "project_id": self.project_id,
                    "artifact_ids": [existing, "art_missing"],
                    "include_content": True,
                },
            )

        self.assertEqual(
            ctx.exception.details["missing_artifact_ids"], ["art_missing"]
        )

    def test_contract_bounds_and_disambiguates_batch_reads(self) -> None:
        parsed = ArtifactFindInput.model_validate(
            {
                "project_id": "proj_1",
                "artifact_ids": ["art_2", "art_1", "art_2"],
            }
        )
        self.assertEqual(parsed.artifact_ids, ["art_2", "art_1"])
        with self.assertRaises(PydanticValidationError):
            ArtifactFindInput.model_validate(
                {
                    "project_id": "proj_1",
                    "artifact_ids": [f"art_{index}" for index in range(51)],
                }
            )
        with self.assertRaises(PydanticValidationError):
            ArtifactFindInput.model_validate(
                {
                    "project_id": "proj_1",
                    "artifact_ids": ["art_1"],
                    "role": "plan",
                }
            )


if __name__ == "__main__":
    unittest.main()

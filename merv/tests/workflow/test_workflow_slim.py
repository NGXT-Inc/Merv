"""The agent-facing `workflow.status_and_next` tool returns a slim projection;
the service method still returns the full shape the UI depends on."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.support.brain import TestBrain
from merv.brain.sandbox.execution.backends.fake import FakeSandboxBackend


# association_version_id is the submission pin — agents confirm a
# re-associate took effect by watching it change, so it stays in the slim view.
SLIM_ARTIFACT_KEYS = {"id", "role", "lens_id", "path", "size_bytes"}
HEAVY_ARTIFACT_KEYS = {"content_sha256", "content_type", "created_by", "created_at", "updated_at", "project_id", "submitted_order"}

class WorkflowSlimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.backend = FakeSandboxBackend()
        self.app = TestBrain(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            execution_backend=self.backend,
        )
        self.project_id = self.call("project", action="create", name="Slim Project")["id"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def call(self, tool: str, **kwargs):
        return self.app.call_tool(tool, kwargs)

    def _set_status(self, exp_id: str, status: str) -> None:
        with self.app.store.transaction() as conn:
            conn.execute("UPDATE experiments SET status = ? WHERE id = ?", (status, exp_id))

    def _seed_review(self, *, exp_id: str, review_id: str, seq: int, **overrides) -> None:
        """Write a review row directly (FK off) with bookkeeping + findings."""
        import json
        import sqlite3
        raw = sqlite3.connect(self.repo / ".research_plugin" / "state.sqlite")
        raw.execute("PRAGMA foreign_keys=OFF")
        cols = [r[1] for r in raw.execute("PRAGMA table_info(reviews)").fetchall()]
        vals = {
            "id": review_id, "project_id": self.project_id, "target_type": "experiment", "target_id": exp_id,
            "role": "experiment_reviewer", "verdict": "pass", "status": "submitted",
            "findings_json": json.dumps([{"issue": "narrow", "severity": "low"}]),
            "evidence_json": json.dumps({"exit_code": 0}), "notes": "looks good",
            "synopsis": f"synopsis for {review_id}",
            "target_snapshot_id": "experiment|" + "x" * 500, "created_at": "2026-06-03T04:41:27Z",
            "request_id": "rr_x", "session_id": "rvs_x", "created_seq": seq,
            **overrides,
        }
        present = {k: v for k, v in vals.items() if k in cols}
        raw.execute(f"INSERT INTO reviews ({','.join(present)}) VALUES ({','.join('?' for _ in present)})", list(present.values()))
        raw.commit(); raw.close()

    def _experiment_with_plan(self) -> str:
        exp_id = self.call(
            "experiment.create",
            name="the-thing",
            project_id=self.project_id,
            intent="Do the thing on the staged subset.\n\nTitle: The Thing",
        )["id"]
        self.app.submit_artifact(
            project_id=self.project_id, target_type="experiment",
            target_id=exp_id, role="plan", path="plan.md", body="planned\n",
        )
        return exp_id

    def test_experiment_scope_is_slim(self) -> None:
        exp_id = self._experiment_with_plan()
        slim = self.call("workflow.status_and_next", project_id=self.project_id, experiment_id=exp_id)

        self.assertEqual(slim["scope"], "experiment")
        self.assertIn("current_gate", slim["workflow"])

        exp = slim["experiment"]
        # The agent sees the experiment's identity: its name.
        self.assertEqual(exp["name"], "the-thing")
        # The duplicate all-attempts `resources` list is gone…
        self.assertNotIn("artifacts", exp)
        self.assertIn("current_attempt_artifacts", exp)
        # …and each resource carries only the light fields.
        res = exp["current_attempt_artifacts"][0]
        self.assertEqual(set(res), SLIM_ARTIFACT_KEYS)
        self.assertEqual(HEAVY_ARTIFACT_KEYS & set(res), set())
        self.assertEqual(res["role"], "plan")
        # tested_claims collapsed to ids; reviews compacted.
        self.assertIn("tested_claim_ids", exp)
        self.assertNotIn("tested_claims", exp)
        self.assertIsInstance(exp["reviews"], list)

        # Project block is a bare reference — no other experiments' intents.
        self.assertEqual(set(slim["project"]), {"id", "name"})

        # No sandbox yet → explicitly says so.
        self.assertFalse(slim["sandbox"]["active"])
        self.assertIn("note", slim["sandbox"])

    def test_reviews_carry_one_body_and_older_tldrs(self) -> None:
        exp_id = self._experiment_with_plan()
        self._seed_review(exp_id=exp_id, review_id="rev_1", seq=1, created_at="2026-06-01T00:00:00Z")
        self._seed_review(exp_id=exp_id, review_id="rev_2", seq=2, created_at="2026-06-03T00:00:00Z")

        slim = self.call("workflow.status_and_next", project_id=self.project_id, experiment_id=exp_id)
        reviews = slim["experiment"]["reviews"]

        self.assertEqual([review["id"] for review in reviews], ["rev_2", "rev_1"])
        self.assertEqual(set(reviews[0]), {"id", "role", "verdict", "created_at", "synopsis",
                                           "findings", "notes", "evidence"})
        self.assertEqual(set(reviews[1]), {"id", "role", "verdict", "created_at", "synopsis"})

    def test_active_sandbox_is_summarized(self) -> None:
        exp_id = self._experiment_with_plan()
        self._set_status(exp_id, "ready_to_run")
        self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id, gpu="A100")

        slim = self.call("workflow.status_and_next", project_id=self.project_id, experiment_id=exp_id)
        sandbox = slim["sandbox"]
        self.assertTrue(sandbox["active"])
        self.assertTrue(sandbox["sandbox_id"])
        self.assertTrue(sandbox["ssh_host"])
        self.assertEqual(sandbox["status"], "running")
        # SSH key material / raw command are NOT here — that's sandbox.request's job.
        self.assertNotIn("key_path", sandbox)

    def test_project_scope_is_compact(self) -> None:
        # With no experiment yet, the tool orients at the project level
        # (`_resolve_scope` only auto-picks an experiment once one exists).
        self.call("claim.create", project_id=self.project_id, statement="Bigger batches help.")
        slim = self.call("workflow.status_and_next", project_id=self.project_id)

        self.assertEqual(slim["scope"], "project")
        self.assertIsNone(slim["experiment"])
        self.assertEqual(slim["workflow"]["current_gate"], "project_setup")
        claim = slim["project"]["claims"][0]
        self.assertEqual(set(claim), {"id", "status", "confidence", "statement"})

    def test_service_method_keeps_full_shape_for_ui(self) -> None:
        exp_id = self._experiment_with_plan()
        full = self.app.workflow.status_and_next(project_id=self.project_id, experiment_id=exp_id)
        # The UI path still gets the rich shape: the all-attempts artifact
        # list with full metadata, and the project-wide experiment list.
        self.assertIn("artifacts", full["experiment"])
        self.assertIn("content_type", full["experiment"]["current_attempt_artifacts"][0])
        self.assertIn("active_experiments", full["project"])


if __name__ == "__main__":
    unittest.main()

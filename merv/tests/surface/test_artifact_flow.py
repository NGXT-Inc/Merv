"""End-to-end artifact submit flow: tool -> token PUT -> gates/reads."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support.brain import TestBrain
from merv.brain.kernel.utils import ValidationError
from merv.brain.surface.transport.api.gateway import RequestAuthenticator
from merv.brain.surface.transport.http_policy import HttpSurfacePolicy

VALID_PLAN = (
    "## Summary\nA toy experiment used by the artifact-flow tests.\n\n"
    "## Objective & hypothesis\nThreshold beats the majority baseline.\n\n"
    "## Evaluation\nAccuracy vs baseline; success if accuracy > 0.6.\n"
)

VALID_REPORT = (
    "## Summary\nRan the toy experiment per the approved plan.\n\n"
    "## Results\nAccuracy 0.72 vs target 0.60.\n\n"
    "## Deviations from plan\nNone.\n\n"
    "## Conclusion\nDecision rule met.\n"
)

VALID_GRAPH = (
    '{"version": 1, "nodes": ['
    '{"id": "obj", "kind": "objective", "label": "Beat baseline"},'
    '{"id": "out", "kind": "outcome", "label": "Met at 0.72"}],'
    ' "edges": [{"from": "obj", "to": "out", "label": "confirmed by"}]}\n'
)


def full_roster() -> list[dict[str, str]]:
    return [
        {"id": "amplify"},
        {"id": "avoid"},
        {"id": "entropy"},
        {
            "id": "rigor",
            "charter": "Methodological soundness of the experiments.",
            "why_distinct": "Judges how we measured, not what we found.",
        },
        {
            "id": "cost",
            "charter": "Compute spent vs information gained per experiment.",
            "why_distinct": "Prices the exploration; no core lens does.",
        },
    ]


class ArtifactFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.app = TestBrain(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
        )
        self.project_id = self.call("project", action="create", name="Artifact Flow")["id"]

    def tearDown(self) -> None:
        self.tmp.cleanup()
        self.app.shutdown()

    def call(self, tool_name: str, **kwargs):
        return self.app.call_tool(tool_name, kwargs)

    def _submit(
        self,
        *,
        target_type: str,
        target_id: str,
        role: str,
        path: str,
        body: str,
        lens_id: str = "",
    ) -> dict:
        pending = self.call(
            "artifact.submit",
            project_id=self.project_id,
            target_type=target_type,
            target_id=target_id,
            role=role,
            path=path,
            lens_id=lens_id,
        )
        token = pending["run"].rsplit("/", 1)[-1].rstrip("'")
        response = self.app._client.put(
            f"/api/artifacts/u/{token}", content=body.encode()
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {**response.json(), "artifact_id": pending["artifact_id"]}

    def _pass_review(self, *, exp_id: str, role: str) -> None:
        req = self.call(
            "review.request",
            project_id=self.project_id,
            target_type="experiment",
            target_id=exp_id,
            role=role,
        )
        session = self.call(
            "review.start",
            review_request_id=req["review_request_id"],
            reviewer_capability=req["reviewer_capability"],
            caller_session_id=f"{role}-reviewer",
        )
        self.call(
            "review.submit",
            review_session_id=session["review_session_id"],
            verdict="pass",
            synopsis="The plan and results check out, so the attempt stands.",
        )

    def test_full_loop_submit_upload_gate_and_transitions(self) -> None:
        exp_id = self.call(
            "experiment.create",
            project_id=self.project_id,
            name="artifact-loop",
            intent="Prove the artifact submit loop.",
        )["id"]
        # Plan gate blocks until the artifact upload lands.
        with self.assertRaises(Exception):
            self.call(
                "experiment.transition",
                project_id=self.project_id,
                experiment_id=exp_id,
                transition="submit_design",
            )
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="plan", path="plan.md", body=VALID_PLAN,
        )
        self.call(
            "experiment.transition", project_id=self.project_id,
            experiment_id=exp_id, transition="submit_design",
        )
        self._pass_review(exp_id=exp_id, role="design_reviewer")
        for transition in ("mark_ready_to_run", "start_running"):
            self.call(
                "experiment.transition", project_id=self.project_id,
                experiment_id=exp_id, transition=transition,
            )
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="result", path="results.json", body='{"accuracy": 0.72}\n',
        )
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="report", path="report.md", body=VALID_REPORT,
        )
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="graph", path="graph.json", body=VALID_GRAPH,
        )
        self.call(
            "experiment.transition", project_id=self.project_id,
            experiment_id=exp_id, transition="submit_results",
        )
        self._pass_review(exp_id=exp_id, role="experiment_reviewer")
        state = self.call(
            "experiment.transition", project_id=self.project_id,
            experiment_id=exp_id, transition="complete",
            evidence={"conclusion": "Threshold met."},
        )
        self.assertEqual(state["status"], "complete")

    def test_resubmit_invalidates_a_pinned_review(self) -> None:
        exp_id = self.call(
            "experiment.create",
            project_id=self.project_id,
            name="resubmit-invalidates",
            intent="Snapshot invalidation.",
        )["id"]
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="plan", path="plan.md", body=VALID_PLAN,
        )
        self.call(
            "experiment.transition", project_id=self.project_id,
            experiment_id=exp_id, transition="submit_design",
        )
        req = self.call(
            "review.request",
            project_id=self.project_id,
            target_type="experiment",
            target_id=exp_id,
            role="design_reviewer",
        )
        # Resubmitting the plan mints a new artifact id -> the pinned snapshot
        # no longer matches and the review session refuses to start.
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="plan", path="plan.md", body=VALID_PLAN + "Revised.\n",
        )
        with self.assertRaises(Exception):
            self.call(
                "review.start",
                review_request_id=req["review_request_id"],
                reviewer_capability=req["reviewer_capability"],
                caller_session_id="design-reviewer",
            )

    def test_report_must_reference_a_pinned_exhibit_by_basename(self) -> None:
        exp_id = self.call(
            "experiment.create",
            project_id=self.project_id,
            name="exhibit-reference",
            intent="Exhibit basename check.",
        )["id"]
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="plan", path="plan.md", body=VALID_PLAN,
        )
        self.call(
            "experiment.transition", project_id=self.project_id,
            experiment_id=exp_id, transition="submit_design",
        )
        self._pass_review(exp_id=exp_id, role="design_reviewer")
        for transition in ("mark_ready_to_run", "start_running"):
            self.call(
                "experiment.transition", project_id=self.project_id,
                experiment_id=exp_id, transition=transition,
            )
        self.app.artifacts.pin_system_artifact(
            path="experiments/exhibit-reference/metrics_exhibit.json",
            experiment_id=exp_id,
            role="exhibit",
            content_bytes=b'{"kind": "metrics_exhibit"}',
            content_type="application/json",
            title="Metrics exhibit",
            project_id=self.project_id,
        )
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="result", path="results.json", body='{"accuracy": 0.72}\n',
        )
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="graph", path="graph.json", body=VALID_GRAPH,
        )
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="report", path="report.md", body=VALID_REPORT,
        )
        with self.assertRaises(Exception) as caught:
            self.call(
                "experiment.transition", project_id=self.project_id,
                experiment_id=exp_id, transition="submit_results",
            )
        self.assertIn("metrics_exhibit.json", str(caught.exception))
        self._submit(
            target_type="experiment", target_id=exp_id,
            role="report", path="report.md",
            body=VALID_REPORT.replace(
                "Accuracy 0.72", "Per metrics_exhibit.json, accuracy 0.72"
            ),
        )
        self.call(
            "experiment.transition", project_id=self.project_id,
            experiment_id=exp_id, transition="submit_results",
        )

    def test_lens_coverage_keys_on_the_explicit_lens_id(self) -> None:
        wave_id = self.call(
            "reflection.create",
            project_id=self.project_id,
            title="Wave",
            lenses=full_roster(),
        )["id"]
        for lens in full_roster():
            self._submit(
                target_type="reflection", target_id=wave_id,
                role="reflection_lens_doc",
                # File names deliberately do NOT match lens ids: coverage must
                # key on the explicit lens_id field.
                path=f"reflections/notes-{lens['id']}-v2.md",
                body=f"# {lens['id']}\nFindings through this lens.\n",
                lens_id=lens["id"],
            )
        state = self.call(
            "reflection.get", project_id=self.project_id, reflection_id=wave_id
        )
        coverage = state["reflection_coverage"]
        self.assertTrue(coverage["complete"], coverage)
        self.call(
            "reflection.transition",
            project_id=self.project_id,
            reflection_id=wave_id,
            transition="submit_reflections",
        )

    def test_figure_follow_up_flow_over_http(self) -> None:
        exp_id = self.call(
            "experiment.create",
            project_id=self.project_id,
            name="figure-flow",
            intent="Figure follow-up uploads.",
        )["id"]
        result = self._submit(
            target_type="experiment", target_id=exp_id,
            role="plan", path="plan.md",
            body=VALID_PLAN + "\n![sketch](figures/sketch.png)\n",
        )
        self.assertEqual(len(result["figures"]), 1)
        figure = result["figures"][0]
        self.assertEqual(figure["link_path"], "figures/sketch.png")
        self.assertIn("/api/artifacts/f/", figure["run"])
        token = figure["run"].rsplit("/", 1)[-1].rstrip("'")
        response = self.app._client.put(
            f"/api/artifacts/f/{token}", content=b"\x89PNG fake"
        )
        self.assertEqual(response.status_code, 200, response.text)
        # The UI figure read serves the submitted bytes.
        fetched = self.app._client.get(
            f"/api/projects/{self.project_id}/artifacts/"
            f"{result['artifact_id']}/figure",
            params={"rel": "figures/sketch.png"},
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.content, b"\x89PNG fake")

    def test_oversize_upload_returns_413(self) -> None:
        exp_id = self.call(
            "experiment.create",
            project_id=self.project_id,
            name="oversize",
            intent="Cap enforcement.",
        )["id"]
        pending = self.call(
            "artifact.submit",
            project_id=self.project_id,
            target_type="experiment",
            target_id=exp_id,
            role="plan",
            path="plan.md",
        )
        token = pending["run"].rsplit("/", 1)[-1].rstrip("'")
        response = self.app._client.put(
            f"/api/artifacts/u/{token}", content=b"x" * 20_000
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["max_bytes"], 16_000)

    def test_ui_read_routes_serve_list_content_and_file(self) -> None:
        exp_id = self.call(
            "experiment.create",
            project_id=self.project_id,
            name="ui-reads",
            intent="Read routes.",
        )["id"]
        submitted = self._submit(
            target_type="experiment", target_id=exp_id,
            role="plan", path="plan.md", body=VALID_PLAN,
        )
        listing = self.app._client.get(
            f"/api/projects/{self.project_id}/artifacts",
            params={"target_type": "experiment", "target_id": exp_id},
        ).json()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["artifacts"][0]["id"], submitted["artifact_id"])

        content = self.app._client.get(
            f"/api/projects/{self.project_id}/artifacts/"
            f"{submitted['artifact_id']}/content"
        ).json()
        self.assertEqual(content["content"], VALID_PLAN)

        file_response = self.app._client.get(
            f"/api/projects/{self.project_id}/artifacts/"
            f"{submitted['artifact_id']}/file"
        )
        self.assertEqual(file_response.content.decode(), VALID_PLAN)
        self.assertIn("text/markdown", file_response.headers["content-type"])

    def test_lens_id_is_required_by_the_tool_contract(self) -> None:
        with self.assertRaises(ValidationError):
            self.call(
                "artifact.submit",
                project_id=self.project_id,
                target_type="reflection",
                target_id="ref_x",
                role="reflection_lens_doc",
                path="rigor.md",
            )


class UploadRouteAuthExemptionTest(unittest.TestCase):
    def _request(self, path: str) -> SimpleNamespace:
        return SimpleNamespace(
            method="PUT",
            state=SimpleNamespace(),
            url=SimpleNamespace(path=path),
            headers={},
        )

    def test_token_upload_paths_bypass_the_bearer_gate(self) -> None:
        class RejectingVerifier:
            def verify_bearer(self, _header):
                raise AssertionError("upload routes must never reach the verifier")

        authenticator = RequestAuthenticator(
            surface=HttpSurfacePolicy.for_surface(
                restrict_cors=True, hosted_control=True
            ),
            verifier=RejectingVerifier(),
        )
        for path in ("/api/artifacts/u/tok_1", "/api/artifacts/f/tok_2"):
            self.assertIsNone(authenticator.authenticate(self._request(path)))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from merv.brain.application.ports.tracking import TrackingCapabilities
from merv.brain.mlflow.tracking import MlflowTrackingContext
from tests.support.brain import TestBrain


VALID_PLAN = (
    "## Summary\nCharacterize transition delivery.\n\n"
    "## Objective & hypothesis\nThe composed workflow preserves its ledger.\n\n"
    "## Evaluation\nThe exact ordered event sequence is the success criterion.\n"
)
VALID_REPORT = (
    "## Summary\nRan the composed transition flow.\n\n"
    "## Results\nThe tracked run is recorded in "
    "[the metrics exhibit](metrics_exhibit.json).\n\n"
    "## Deviations from plan\nNone.\n\n"
    "## Conclusion\nThe ordered ledger remained canonical.\n"
)
VALID_GRAPH = (
    '{"version":1,"nodes":['
    '{"id":"start","kind":"objective","label":"Start"},'
    '{"id":"done","kind":"outcome","label":"Complete"}],'
    '"edges":[{"from":"start","to":"done","label":"then"}]}\n'
)
REVIEW_SYNOPSIS = "The submitted attempt matches its pinned evidence and can stand."


class RecordingTracking:
    """Product adapter double around otherwise-real application composition."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.finalize_calls: list[dict[str, Any]] = []
        self.context_calls = 0
        self.results_calls = 0
        self.runs: list[dict[str, Any]] = []

    def capabilities(self) -> TrackingCapabilities:
        return TrackingCapabilities(logging=True, control=True, readback=True)

    def context(
        self,
        *,
        project_id: str,
        experiment_id: str,
        include_credentials: bool = False,
    ) -> MlflowTrackingContext:
        self.context_calls += 1
        return MlflowTrackingContext(
            configured=True,
            mode="external",
            tracking_uri="https://tracking.test",
            dashboard_url="https://tracking.test",
            experiment_name=f"merv/{project_id}/{experiment_id}",
            env={
                "MLFLOW_TRACKING_URI": "https://tracking.test",
                "MLFLOW_EXPERIMENT_NAME": f"merv/{project_id}/{experiment_id}",
                "RP_PROJECT_ID": project_id,
                "RP_EXPERIMENT_ID": experiment_id,
            },
        )

    def create_run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        attempt_index: int,
        run_name: str,
    ) -> dict[str, Any]:
        self.create_calls.append(
            {
                "project_id": project_id,
                "experiment_id": experiment_id,
                "attempt_index": attempt_index,
                "run_name": run_name,
            }
        )
        run_id = "run-composed"
        self.runs = [
            {
                "run_id": run_id,
                "run_name": run_name,
                "status": "RUNNING",
                "start_time": int(time.time() * 1000),
                "end_time": 0,
                "params": {"seed": "7"},
                "tags": {
                    "project_id": project_id,
                    "experiment_id": experiment_id,
                },
                "metrics": {"accuracy": {"last": 0.75, "step": 1}},
            }
        ]
        return {
            "created": True,
            "configured": True,
            "control_configured": True,
            "experiment_name": f"merv/{project_id}/{experiment_id}",
            "experiment_id": "tracking-exp-1",
            "run_id": run_id,
            "run_name": run_name,
            "status": "RUNNING",
            "artifact_uri": "s3://tracking/run-composed",
            "created_at": "2026-07-19T12:00:00Z",
        }

    def finalize_run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        run_id: str,
        status: str,
        wait_seconds: float,
    ) -> dict[str, Any]:
        self.finalize_calls.append(
            {
                "project_id": project_id,
                "experiment_id": experiment_id,
                "run_id": run_id,
                "status": status,
                "wait_seconds": wait_seconds,
            }
        )
        self.runs[0]["status"] = status
        self.runs[0]["end_time"] = int(time.time() * 1000)
        return {
            "configured": True,
            "control_configured": True,
            "run_id": run_id,
            "requested_status": status,
            "terminal": True,
            "run": {
                "run_id": run_id,
                "run_name": self.runs[0]["run_name"],
                "status": status,
                "artifact_uri": "s3://tracking/run-composed",
                "created_at": "2026-07-19T12:00:00Z",
            },
        }

    def results_metrics(
        self, *, project_id: str, experiment_id: str
    ) -> dict[str, Any]:
        self.results_calls += 1
        return {
            "available": True,
            "source": "mlflow",
            "experiment_id": experiment_id,
            "experiments": [
                {
                    "experiment_id": "tracking-exp-1",
                    "name": f"merv/{project_id}/{experiment_id}",
                    "runs": [dict(run) for run in self.runs],
                }
            ],
        }


def _cursor(app: TestBrain) -> int:
    conn = app._store.connect()
    try:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM events").fetchone()
        return int(row["id"])
    finally:
        conn.close()


def _ledger_delta(
    testcase: unittest.TestCase,
    app: TestBrain,
    *,
    project_id: str,
    after_id: int,
) -> list[tuple[str, str, str, dict[str, Any]]]:
    conn = app._store.connect()
    try:
        raw = conn.execute(
            """
            SELECT id, project_id, type, target_type, target_id, payload_json
            FROM events
            WHERE project_id = ? AND id > ?
            ORDER BY id
            """,
            (project_id, after_id),
        ).fetchall()
    finally:
        conn.close()
    testcase.assertEqual(
        [int(row["id"]) for row in raw],
        list(range(after_id + 1, after_id + len(raw) + 1)),
    )
    rows: list[tuple[str, str, str, dict[str, Any]]] = []
    for row in raw:
        testcase.assertEqual(str(row["project_id"]), project_id)
        payload_json = str(row["payload_json"])
        payload = json.loads(payload_json)
        testcase.assertEqual(payload_json, json.dumps(payload, sort_keys=True))
        rows.append(
            (
                str(row["type"]),
                str(row["target_type"]),
                str(row["target_id"]),
                payload,
            )
        )
    return rows


def _normalized(value: Any, *, project_id: str, experiment_id: str) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "<timestamp>"
                if key in {"created_at", "updated_at"}
                else _normalized(
                    item, project_id=project_id, experiment_id=experiment_id
                )
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _normalized(item, project_id=project_id, experiment_id=experiment_id)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _normalized(item, project_id=project_id, experiment_id=experiment_id)
            for item in value
        )
    if isinstance(value, str):
        return value.replace(project_id, "<project>").replace(
            experiment_id, "<experiment>"
        )
    return value


def _row(
    event_type: str,
    target_id: str,
    payload: dict[str, Any],
    *,
    target_type: str = "experiment",
) -> tuple[str, str, str, dict[str, Any]]:
    return event_type, target_type, target_id, payload


def _transition_row(
    experiment_id: str, *, before: str, after: str, transition: str
) -> tuple[str, str, str, dict[str, Any]]:
    return _row(
        "experiment.transitioned",
        experiment_id,
        {"evidence": {}, "from": before, "to": after, "transition": transition},
    )


def _tracking_row(
    experiment_id: str, *, event_type: str, status: str, previous: str
) -> tuple[str, str, str, dict[str, Any]]:
    return _row(
        event_type,
        experiment_id,
        {
            "error": "",
            "previous_run_id": previous,
            "run_id": "run-composed",
            "run_name": f"{experiment_id}-attempt-1",
            "status": status,
        },
    )


class TransitionDeliveryAndLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _brain(self, tracking: RecordingTracking) -> TestBrain:
        return TestBrain(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            mlflow_tracking=tracking,
        )

    def _register(
        self,
        app: TestBrain,
        *,
        project_id: str,
        experiment_id: str,
        path: str,
        role: str,
        body: str,
    ) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        app.call_tool(
            "resource.register",
            {
                "project_id": project_id,
                "path": path,
                "kind": role,
                "target_type": "experiment",
                "target_id": experiment_id,
                "role": role,
            },
        )

    def _pass_review(
        self,
        app: TestBrain,
        *,
        project_id: str,
        experiment_id: str,
        role: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        requested = app.call_tool(
            "review.request",
            {
                "project_id": project_id,
                "target_type": "experiment",
                "target_id": experiment_id,
                "role": role,
            },
        )
        started = app.call_tool(
            "review.start",
            {
                "review_request_id": requested["review_request_id"],
                "reviewer_capability": requested["reviewer_capability"],
                "caller_session_id": f"{role}-reviewer",
            },
        )
        submitted = app.call_tool(
            "review.submit",
            {
                "review_session_id": started["review_session_id"],
                "verdict": "pass",
                "synopsis": REVIEW_SYNOPSIS,
            },
        )
        return requested, started, submitted

    def test_rest_and_mcp_start_running_have_equivalent_response_and_ledger_delta(
        self,
    ) -> None:
        tracking = RecordingTracking()
        app = self._brain(tracking)
        client = TestClient(app.fastapi_app)
        targets: list[tuple[str, str]] = []
        for name in ("MCP Parity", "REST Parity"):
            project_id = app.call_tool("project", {"action": "create", "name": name})[
                "id"
            ]
            experiment_id = app.call_tool(
                "experiment.create",
                {
                    "project_id": project_id,
                    "name": "equivalent-start",
                    "intent": "Prove delivery parity.",
                },
            )["id"]
            with app._store.transaction() as conn:
                conn.execute(
                    "UPDATE experiments SET status = 'ready_to_run' WHERE id = ?",
                    (experiment_id,),
                )
            targets.append((project_id, experiment_id))

        mcp_project, mcp_experiment = targets[0]
        mcp_cursor = _cursor(app)
        mcp_http = client.post(
            "/mcp/call",
            json={
                "name": "experiment.transition",
                "arguments": {
                    "project_id": mcp_project,
                    "experiment_id": mcp_experiment,
                    "transition": "start_running",
                },
            },
        )
        self.assertEqual(mcp_http.status_code, 200, mcp_http.text)
        mcp_response = mcp_http.json()["result"]
        mcp_rows = _ledger_delta(
            self, app, project_id=mcp_project, after_id=mcp_cursor
        )

        rest_project, rest_experiment = targets[1]
        rest_cursor = _cursor(app)
        rest_http = client.post(
            f"/api/projects/{rest_project}/experiments/{rest_experiment}/transition",
            json={"transition": "start_running"},
        )
        self.assertEqual(rest_http.status_code, 200, rest_http.text)
        rest_response = rest_http.json()
        rest_rows = _ledger_delta(
            self, app, project_id=rest_project, after_id=rest_cursor
        )

        self.assertEqual(
            _normalized(
                mcp_response,
                project_id=mcp_project,
                experiment_id=mcp_experiment,
            ),
            _normalized(
                rest_response,
                project_id=rest_project,
                experiment_id=rest_experiment,
            ),
        )
        expected = lambda experiment_id: [
            _transition_row(
                experiment_id,
                before="ready_to_run",
                after="running",
                transition="start_running",
            ),
            _tracking_row(
                experiment_id,
                event_type="experiment.mlflow_run_created",
                status="RUNNING",
                previous="",
            ),
        ]
        self.assertEqual(mcp_rows, expected(mcp_experiment))
        self.assertEqual(rest_rows, expected(rest_experiment))
        self.assertEqual(
            _normalized(
                mcp_rows, project_id=mcp_project, experiment_id=mcp_experiment
            ),
            _normalized(
                rest_rows, project_id=rest_project, experiment_id=rest_experiment
            ),
        )

    def test_real_composition_emits_exact_canonical_transition_ledger_without_recursion(
        self,
    ) -> None:
        tracking = RecordingTracking()
        app = self._brain(tracking)
        project_id = app.call_tool(
            "project", {"action": "create", "name": "Canonical Ledger"}
        )["id"]
        experiment_id = app.call_tool(
            "experiment.create",
            {
                "project_id": project_id,
                "name": "ledger-flow",
                "intent": "Drive the real composed workflow.",
            },
        )["id"]
        self._register(
            app,
            project_id=project_id,
            experiment_id=experiment_id,
            path="plan.md",
            role="plan",
            body=VALID_PLAN,
        )
        app.call_tool(
            "experiment.transition",
            {
                "project_id": project_id,
                "experiment_id": experiment_id,
                "transition": "submit_design",
            },
        )
        self._pass_review(
            app,
            project_id=project_id,
            experiment_id=experiment_id,
            role="design_reviewer",
        )
        app.call_tool(
            "experiment.transition",
            {
                "project_id": project_id,
                "experiment_id": experiment_id,
                "transition": "mark_ready_to_run",
            },
        )
        for path, role, body in (
            ("results.json", "result", '{"accuracy":0.75}\n'),
            ("report.md", "report", VALID_REPORT),
            ("graph.json", "graph", VALID_GRAPH),
        ):
            self._register(
                app,
                project_id=project_id,
                experiment_id=experiment_id,
                path=path,
                role=role,
                body=body,
            )

        cursor = _cursor(app)
        dispatch_patcher = patch.object(
            app.transition_experiment.dispatcher,
            "dispatch",
            wraps=app.transition_experiment.dispatcher.dispatch,
        )
        dispatch = dispatch_patcher.start()
        self.addCleanup(dispatch_patcher.stop)
        started = app.call_tool(
            "experiment.transition",
            {
                "project_id": project_id,
                "experiment_id": experiment_id,
                "transition": "start_running",
            },
        )
        submitted = app.call_tool(
            "experiment.transition",
            {
                "project_id": project_id,
                "experiment_id": experiment_id,
                "transition": "submit_results",
            },
        )
        request, session, review = self._pass_review(
            app,
            project_id=project_id,
            experiment_id=experiment_id,
            role="experiment_reviewer",
        )
        completed = app.call_tool(
            "experiment.transition",
            {
                "project_id": project_id,
                "experiment_id": experiment_id,
                "transition": "complete",
            },
        )

        self.assertEqual(started["mlflow_run"]["status"], "RUNNING")
        self.assertTrue(submitted["metrics_exhibit"]["pinned"])
        self.assertEqual(submitted["mlflow_run"]["status"], "FINISHED")
        self.assertEqual(completed["status"], "complete")

        conn = app._store.connect()
        try:
            exhibit_link = conn.execute(
                """
                SELECT a.resource_id, a.version_id, r.path
                FROM resource_associations a
                JOIN resources r ON r.id = a.resource_id
                WHERE a.target_type = 'experiment' AND a.target_id = ?
                  AND a.role = 'exhibit'
                ORDER BY a.created_seq DESC LIMIT 1
                """,
                (experiment_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(exhibit_link)
        resource_id = str(exhibit_link["resource_id"])
        version_id = str(exhibit_link["version_id"])
        exhibit_path = str(exhibit_link["path"])
        review_id = str(review["id"])

        expected = [
            _transition_row(
                experiment_id,
                before="ready_to_run",
                after="running",
                transition="start_running",
            ),
            _tracking_row(
                experiment_id,
                event_type="experiment.mlflow_run_created",
                status="RUNNING",
                previous="",
            ),
            _row(
                "experiment.exhibit_generated",
                experiment_id,
                {
                    "attempt_index": 1,
                    "mlflow": {
                        "available": True,
                        "configured": True,
                        "experiment_name": f"merv/{project_id}/{experiment_id}",
                        "runs_excluded_by_window": 0,
                    },
                    "pinned": True,
                    "result_files": 1,
                    "runs_found": 1,
                },
            ),
            _row(
                "resource.versioned",
                resource_id,
                {"path": exhibit_path, "version_id": version_id},
                target_type="resource",
            ),
            _row(
                "resource.associated",
                experiment_id,
                {
                    "attempt_index": 1,
                    "resource_id": resource_id,
                    "role": "exhibit",
                    "version_id": version_id,
                },
            ),
            _transition_row(
                experiment_id,
                before="running",
                after="experiment_review",
                transition="submit_results",
            ),
            _tracking_row(
                experiment_id,
                event_type="experiment.mlflow_run_refreshed",
                status="FINISHED",
                previous="run-composed",
            ),
            _row(
                "review.requested",
                experiment_id,
                {
                    "request_id": request["review_request_id"],
                    "role": "experiment_reviewer",
                    "superseded_request_ids": [],
                },
            ),
            _row(
                "review.started",
                experiment_id,
                {
                    "request_id": request["review_request_id"],
                    "role": "experiment_reviewer",
                    "session_id": session["review_session_id"],
                },
            ),
            _row(
                "review.submitted",
                experiment_id,
                {
                    "return_to": "",
                    "review_id": review_id,
                    "role": "experiment_reviewer",
                    "synopsis": REVIEW_SYNOPSIS,
                    "verdict": "pass",
                },
            ),
            _transition_row(
                experiment_id,
                before="experiment_review",
                after="complete",
                transition="complete",
            ),
        ]
        rows = _ledger_delta(self, app, project_id=project_id, after_id=cursor)
        self.assertEqual(rows, expected)
        self.assertEqual(
            [row[0] for row in rows].count("experiment.mlflow_run_created"),
            1,
        )
        self.assertEqual(
            [row[0] for row in rows].count("experiment.mlflow_run_refreshed"),
            1,
        )
        self.assertFalse(
            any("dispatch" in row[0] or "ack" in row[0] for row in rows)
        )
        self.assertEqual(
            [
                (call.kwargs["event"].type, call.kwargs["phase"])
                for call in dispatch.call_args_list
            ],
            [
                ("experiment.transitioned", "post_commit"),
                ("experiment.transitioned", "post_response"),
            ]
            * 3,
        )
        self.assertEqual(len(tracking.create_calls), 1)
        self.assertEqual(len(tracking.finalize_calls), 1)
        self.assertEqual(tracking.finalize_calls[0]["status"], "FINISHED")
        self.assertEqual(tracking.results_calls, 1)
        self.assertEqual(tracking.context_calls, 3)


if __name__ == "__main__":
    unittest.main()

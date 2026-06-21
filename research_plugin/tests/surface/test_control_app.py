from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.composition.control_mode import build_control_app
from backend.execution.backends.fake import FakeSandboxBackend
from backend.http_api import create_fastapi_app


class ControlAppTest(unittest.TestCase):
    def test_control_app_records_scoped_activity_without_local_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _queue, auth = build_control_app(
                repo_root=Path(tmp),
                execution_backend=FakeSandboxBackend(),
            )
            self.addCleanup(app.shutdown)
            token = auth.mint_token(tenant_id="acme")
            headers = {"Authorization": f"Bearer {token}"}
            client = TestClient(
                create_fastapi_app(app=app, auth=auth),
                raise_server_exceptions=False,
            )

            created = client.post(
                "/api/projects", json={"name": "Control Telemetry"}, headers=headers
            )
            self.assertEqual(created.status_code, 201, created.text)
            project_id = created.json()["id"]
            claim = client.post(
                f"/api/projects/{project_id}/claims",
                json={"statement": "A scoped control-plane claim."},
                headers=headers,
            )
            self.assertEqual(claim.status_code, 201, claim.text)

            stats = app.tool_calls.stats(project_id=project_id)
            self.assertGreaterEqual(stats["totals"]["calls"], 1)
            self.assertIn("filter", stats)
            app.tool_calls.record(
                tool="review.start",
                source="http",
                status="ok",
                duration_ms=1,
                arguments={
                    "project_id": project_id,
                    "reviewer_capability": "rp_arg",
                },
                result={"capability": "rp_result"},
            )
            listed = client.get(
                "/api/debug/tool-calls?source=all&status=all",
                headers=headers,
            )
            self.assertEqual(listed.status_code, 200, listed.text)
            calls = listed.json()["calls"]
            self.assertGreaterEqual(len(calls), 1)
            self.assertTrue(listed.json()["by_tool"])
            review_call = next(call for call in calls if call["tool"] == "review.start")
            detail = client.get(
                f"/api/debug/tool-calls/{review_call['id']}",
                headers=headers,
            )
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["args"]["reviewer_capability"], "[redacted]")
            self.assertEqual(detail.json()["result"]["capability"], "[redacted]")
            activity = client.get("/api/activity", headers=headers)
            self.assertEqual(activity.status_code, 200, activity.text)
            self.assertGreaterEqual(activity.json()["summary"]["total"], 1)
            names = {tool["name"] for tool in app.list_tools()}
            self.assertIn("claim.create", names)
            self.assertNotIn("resource.register_file", names)


if __name__ == "__main__":
    unittest.main()

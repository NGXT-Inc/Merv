from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fastapi.testclient import TestClient

from backend.app import ResearchPluginApp
from backend.http_api import create_fastapi_app
from backend.http_server import make_http_server
from backend.execution.backends.fake import FakeSandboxBackend
from mcp_server.time_utils import now_iso


class ResearchPluginHttpApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.backend = FakeSandboxBackend()
        self.app = ResearchPluginApp(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            execution_backend=self.backend,
        )
        self.client = TestClient(create_fastapi_app(self.app))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None):
        response = self.client.request(method, path, json=body)
        self.assertLess(response.status_code, 400, response.text)
        return response.json()

    def test_home_claim_experiment_resource_review_endpoints(self) -> None:
        project = self.request("POST", "/api/projects", {"name": "UI Project", "summary": "Frontend target"})
        project_id = project["id"]
        claim = self.request(
            "POST",
            f"/api/projects/{project_id}/claims",
            {"statement": "Threshold classifier improves toy accuracy."},
        )
        exp = self.request(
            "POST",
            f"/api/projects/{project_id}/experiments",
            {"intent": "Compare threshold with baseline.", "claim_ids": [claim["id"]]},
        )
        exp_id = exp["id"]
        (self.repo / "plan.md").write_text("metric: accuracy\nbaseline: majority\n")
        resource = self.request(
            "POST",
            f"/api/projects/{project_id}/resources",
            {"path": "plan.md", "kind": "note", "title": "Plan"},
        )
        self.request(
            "POST",
            f"/api/projects/{project_id}/resources/{resource['id']}/associate",
            {"target_type": "experiment", "target_id": exp_id, "role": "plan"},
        )

        home = self.request("GET", f"/api/projects/{project_id}/home")
        self.assertEqual(home["project"]["name"], "UI Project")
        self.assertEqual(home["stats"]["claims"], 1)
        self.assertEqual(home["workflow"]["next_action"], "submit_design_for_review")

        content = self.request("GET", f"/api/projects/{project_id}/resources/{resource['id']}/content")
        self.assertIn("accuracy", content["content"])
        history = self.request("GET", f"/api/projects/{project_id}/resources/{resource['id']}/history")
        version_id = history["versions"][0]["id"]
        self.assertTrue(version_id)

        self.request("POST", f"/api/projects/{project_id}/experiments/{exp_id}/transition", {"transition": "submit_design"})
        review_request = self.request(
            "POST",
            f"/api/projects/{project_id}/reviews/request",
            {"target_type": "experiment", "target_id": exp_id, "role": "design_reviewer"},
        )
        self.assertEqual(review_request["role"], "design_reviewer")
        self.assertEqual(review_request["target_snapshot"]["resources"][0]["version_id"], version_id)
        reviews = self.request("GET", f"/api/projects/{project_id}/reviews?target_type=experiment&target_id={exp_id}")
        self.assertEqual(len(reviews["requests"]), 1)
        self.assertEqual(reviews["requests"][0]["target_snapshot"]["resources"][0]["version_id"], version_id)
        queue = self.request("GET", f"/api/projects/{project_id}/reviews")
        self.assertEqual(queue["requests"][0]["target_snapshot"]["resources"][0]["version_id"], version_id)

    def test_review_start_and_submit_are_scoped_to_route_project(self) -> None:
        project = self.request("POST", "/api/projects", {"name": "Scoped A"})
        pid = project["id"]
        exp = self.request("POST", f"/api/projects/{pid}/experiments", {"intent": "Scoped review"})
        exp_id = exp["id"]
        (self.repo / "plan.md").write_text("plan\n")
        plan = self.request("POST", f"/api/projects/{pid}/resources", {"path": "plan.md", "kind": "plan"})
        self.request("POST", f"/api/projects/{pid}/resources/{plan['id']}/associate", {"target_type": "experiment", "target_id": exp_id, "role": "plan"})
        self.request("POST", f"/api/projects/{pid}/experiments/{exp_id}/transition", {"transition": "submit_design"})
        req = self.request("POST", f"/api/projects/{pid}/reviews/request", {"target_type": "experiment", "target_id": exp_id, "role": "design_reviewer"})

        other = self.request("POST", "/api/projects", {"name": "Scoped B"})
        other_id = other["id"]

        # Starting the review under the wrong project's URL is rejected.
        wrong_start = self.client.request(
            "POST",
            f"/api/projects/{other_id}/reviews/start",
            json={"review_request_id": req["review_request_id"], "reviewer_capability": req["reviewer_capability"], "caller_session_id": "rev"},
        )
        self.assertEqual(wrong_start.status_code, 404, wrong_start.text)

        # The correct project still works.
        session = self.request(
            "POST",
            f"/api/projects/{pid}/reviews/start",
            {"review_request_id": req["review_request_id"], "reviewer_capability": req["reviewer_capability"], "caller_session_id": "rev"},
        )

        wrong_submit = self.client.request(
            "POST",
            f"/api/projects/{other_id}/reviews/submit",
            json={"review_session_id": session["review_session_id"], "verdict": "pass"},
        )
        self.assertEqual(wrong_submit.status_code, 404, wrong_submit.text)

        # Submitting under the owning project still works.
        self.request("POST", f"/api/projects/{pid}/reviews/submit", {"review_session_id": session["review_session_id"], "verdict": "pass"})

    def test_claim_update_http_endpoint(self) -> None:
        project = self.request("POST", "/api/projects", {"name": "Claim Update"})
        pid = project["id"]
        claim = self.request("POST", f"/api/projects/{pid}/claims", {"statement": "X improves Y."})
        updated = self.request("PATCH", f"/api/projects/{pid}/claims/{claim['id']}", {"status": "supported", "confidence": "high"})
        self.assertEqual(updated["status"], "supported")
        self.assertEqual(updated["confidence"], "high")

    def test_project_sync_exclusions_roundtrip_and_reset(self) -> None:
        project = self.request("POST", "/api/projects", {"name": "Sync Config"})
        pid = project["id"]
        self.assertIn("node_modules", project["sync_exclusions"]["names"])
        self.assertIn("data/raw", project["sync_exclusions"]["prefixes"])

        updated = self.request(
            "PATCH",
            f"/api/projects/{pid}",
            {
                "sync_exclusions": {
                    "names": ["custom_cache"],
                    "prefixes": ["datasets/local"],
                    "suffixes": [".bin"],
                }
            },
        )
        self.assertEqual(updated["sync_exclusions_source"], "project")
        self.assertEqual(updated["sync_exclusions"]["names"], ["custom_cache"])
        self.assertEqual(updated["sync_exclusions"]["prefixes"], ["datasets/local"])
        self.assertEqual(updated["sync_exclusions"]["suffixes"], [".bin"])

        reset = self.request(
            "PATCH",
            f"/api/projects/{pid}",
            {"reset_sync_exclusions": True},
        )
        self.assertNotEqual(reset["sync_exclusions_source"], "project")
        self.assertIn("node_modules", reset["sync_exclusions"]["names"])

    def test_project_settings_tools_expose_sync_exclusions_to_agents(self) -> None:
        project = self.app.call_tool("project.create", {"name": "Agent Settings"})
        pid = project["id"]

        settings = self.app.call_tool("project.get_settings", {"project_id": pid})
        self.assertEqual(settings["project_id"], pid)
        self.assertIn("node_modules", settings["sync_exclusions"]["names"])
        self.assertEqual(settings["config_file"], ".research_plugin/sync_exclusions.json")

        updated = self.app.call_tool(
            "project.update_settings",
            {
                "project_id": pid,
                "sync_exclusions": {
                    "names": ["agent_cache"],
                    "paths": ["runs/tmp"],
                    "suffixes": [".tmp"],
                },
            },
        )
        self.assertEqual(updated["sync_exclusions_source"], "project")
        self.assertEqual(updated["sync_exclusions"]["names"], ["agent_cache"])
        self.assertEqual(updated["sync_exclusions"]["prefixes"], ["runs/tmp"])
        self.assertEqual(updated["sync_exclusions"]["suffixes"], [".tmp"])

    def test_sandbox_http_endpoints(self) -> None:
        project = self.request("POST", "/api/projects", {"name": "Sandbox UI Project"})
        project_id = project["id"]
        exp = self.request("POST", f"/api/projects/{project_id}/experiments", {"intent": "Run an experiment"})
        exp_id = exp["id"]
        # Drive the experiment to ready_to_run so a sandbox may be requested.
        with self.app.store.transaction() as conn:
            conn.execute("UPDATE experiments SET status = 'ready_to_run' WHERE id = ?", (exp_id,))
        # Procuring is an agent action (MCP tool); the UI observes the result.
        requested = self.app.call_tool(
            "sandbox.request", {"project_id": project_id, "experiment_id": exp_id, "gpu": "A100"}
        )
        self.assertEqual(requested["status"], "running")
        self.assertEqual(requested["ssh"]["command"], f".research_plugin/sbx {exp_id}")
        self.assertTrue(requested["ssh"]["raw_command"].startswith("ssh -i "))

        sandbox = self.request("GET", f"/api/projects/{project_id}/experiments/{exp_id}/sandbox")
        self.assertEqual(sandbox["status"], "running")
        self.assertTrue(sandbox["sandbox_id"])

        listed = self.request("GET", f"/api/projects/{project_id}/sandboxes")["sandboxes"]
        self.assertEqual(len(listed), 1)

        # Live usage metrics endpoint surfaces the in-container sample.
        self.backend.metrics[requested["sandbox_id"]] = {
            "cpu": {"used_cores": 1.0, "limit_cores": 2.0},
            "memory": {"used_bytes": 1073741824, "limit_bytes": 8589934592},
            "gpus": [{"index": 0, "name": "A100", "util_pct": 10, "mem_used_mib": 512, "mem_total_mib": 40960}],
        }
        metrics = self.request("GET", f"/api/projects/{project_id}/experiments/{exp_id}/sandbox/metrics")
        self.assertTrue(metrics["available"])
        self.assertEqual(metrics["metrics"]["gpus"][0]["util_pct"], 10)

        self.backend.append_transcript(experiment_id=exp_id, text="$ ls\nplan.md\n")
        terminal = self.request("GET", f"/api/projects/{project_id}/experiments/{exp_id}/sandbox/terminal")
        self.assertIn("plan.md", terminal["transcript"])

        released = self.request("POST", f"/api/projects/{project_id}/experiments/{exp_id}/sandbox/release")
        self.assertEqual(released["status"], "terminated")

        self.assertTrue(self.request("GET", "/api/sandboxes/health")["ok"])

    def test_home_exposes_active_experiments_and_processes(self) -> None:
        project = self.request("POST", "/api/projects", {"name": "Active Work Project"})
        project_id = project["id"]
        planned = self.request("POST", f"/api/projects/{project_id}/experiments", {"intent": "Planned active work"})
        running = self.request("POST", f"/api/projects/{project_id}/experiments", {"intent": "Running active work"})
        complete = self.request("POST", f"/api/projects/{project_id}/experiments", {"intent": "Finished work"})
        now = now_iso()
        with self.app.store.transaction() as conn:
            conn.execute("UPDATE experiments SET status = 'running', updated_at = ? WHERE id = ?", (now, running["id"]))
            conn.execute("UPDATE experiments SET status = 'complete', updated_at = ? WHERE id = ?", (now, complete["id"]))
            conn.execute(
                """
                INSERT INTO sandboxes (
                  experiment_id, project_id, sandbox_id, status, created_at, updated_at
                )
                VALUES (?, ?, 'sb_active', 'running', ?, ?)
                """,
                (running["id"], project_id, now, now),
            )
            conn.execute(
                """
                INSERT INTO sandboxes (
                  experiment_id, project_id, sandbox_id, status, terminated_at, created_at, updated_at
                )
                VALUES (?, ?, 'sb_done', 'terminated', ?, ?, ?)
                """,
                (complete["id"], project_id, now, now, now),
            )

        home = self.request("GET", f"/api/projects/{project_id}/home")

        self.assertEqual([item["id"] for item in home["active_experiments"]], [running["id"], planned["id"]])
        self.assertEqual(home["active_experiment"]["id"], running["id"])
        self.assertEqual(home["workflow"]["next_action"], "run_experiment_and_sync_results")
        self.assertEqual(home["stats"]["active_experiments"], 2)
        self.assertEqual(home["stats"]["active_processes"], 1)
        self.assertEqual(home["active_processes"][0]["experiment_id"], running["id"])
        self.assertEqual(home["active_processes"][0]["process_type"], "sandbox")
        self.assertEqual(home["active_processes"][0]["experiment"]["id"], running["id"])
        self.assertNotIn(complete["id"], [item["id"] for item in home["active_experiments"]])

    def test_activity_endpoint_reports_recent_tool_calls(self) -> None:
        self.request("GET", "/api/projects")
        activity = self.request("GET", "/api/activity?limit=5")
        self.assertEqual(activity["activity_log"], str(self.app.activity.log_path))
        self.assertTrue(
            any(
                event.get("event") == "tool.call"
                and event.get("source") == "http"
                and event.get("tool") == "project.list"
                and "projects" in event.get("result", {})
                for event in activity["events"]
            )
        )

    def test_tool_call_stats_endpoint(self) -> None:
        # Generate a few tool calls of differing result sizes via the HTTP path.
        self.request("GET", "/api/projects")
        self.request("GET", "/api/projects")
        stats = self.request("GET", "/api/debug/tool-calls?limit=50")
        self.assertGreaterEqual(stats["totals"]["calls"], 2)
        tools = {row["tool"]: row for row in stats["by_tool"]}
        self.assertIn("project.list", tools)
        self.assertGreater(tools["project.list"]["received_chars"], 0)
        # Aggregate carries distribution stats, per-call rows carry sizes + an id.
        self.assertIn("p95_received_chars", tools["project.list"])
        self.assertTrue(all({"id", "received_chars", "sent_chars"} <= set(c) for c in stats["calls"]))

    def test_tool_call_detail_and_clear(self) -> None:
        self.request("GET", "/api/projects")
        stats = self.request("GET", "/api/debug/tool-calls?tool=project.list")
        call_id = stats["calls"][0]["id"]
        detail = self.request("GET", f"/api/debug/tool-calls/{call_id}")
        # Full raw response is returned as native JSON.
        self.assertEqual(detail["tool"], "project.list")
        self.assertIn("projects", detail["result"])
        self.assertIsInstance(detail["args"], dict)
        # Filtering by an unknown source yields nothing; clear wipes the store.
        self.assertEqual(self.request("GET", "/api/debug/tool-calls?source=nope")["totals"]["calls"], 0)
        cleared = self.request("POST", "/api/debug/tool-calls/clear")
        self.assertGreaterEqual(cleared["cleared"], 1)
        self.assertEqual(self.request("GET", "/api/debug/tool-calls")["totals"]["calls"], 0)

    def test_live_http_server_smoke(self) -> None:
        server = make_http_server(self.app, "127.0.0.1", 0)
        host, port = server.server_address
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://{host}:{port}"
            health = self.fetch_json(base + "/health")
            self.assertTrue(health["ok"])
            project = self.fetch_json(
                base + "/api/projects",
                method="POST",
                body={"name": "Live UI Project"},
            )
            home = self.fetch_json(base + f"/api/projects/{project['id']}/home")
            self.assertEqual(home["project"]["name"], "Live UI Project")
            activity = self.fetch_json(base + "/api/activity?limit=20")
            self.assertTrue(any(event.get("event") == "http.request" for event in activity["events"]))
        finally:
            server.shutdown()
            server.server_close()

    def fetch_json(self, url: str, *, method: str = "GET", body: dict | None = None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=5) as res:
                return json.loads(res.read().decode("utf-8"))
        except HTTPError as exc:
            self.fail(exc.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()

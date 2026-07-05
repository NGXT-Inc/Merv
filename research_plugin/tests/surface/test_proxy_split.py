"""Split-mode proxy routing after the daemon diet.

Split mode now has one HTTP upstream: hosted control. The stdio proxy performs
local data-plane file reads itself, resolves repo→project links from the local
SQLite link file, and forwards explicit facts/bytes to control.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from backend.app import ResearchPluginApp
from backend.execution.backends.fake import FakeSandboxBackend
from backend.transport.http_api import create_fastapi_app
from mcp_server.project_links import ProjectLinks
from mcp_server.proxy import HttpProxyMcpServer, ProxyConfig


class _ControlHarness:
    def __init__(self, *, app: ResearchPluginApp, repo: Path) -> None:
        del repo
        self.url = "http://control.test"
        self.client = TestClient(create_fastapi_app(app=app))

    def http_get(self, *, url: str, is_cloud: bool) -> dict:  # noqa: ARG002
        response = self.client.get(urlsplit(url).path)
        response.raise_for_status()
        return response.json()

    def http_post(self, *, url: str, payload: dict, is_cloud: bool, timeout=None) -> dict:  # noqa: ANN001, ARG002
        response = self.client.post(urlsplit(url).path, json=payload)
        response.raise_for_status()
        return response.json()


class SplitProxyLocalDataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        db_path = self.repo / ".research_plugin" / "state.sqlite"
        self.app = ResearchPluginApp(
            repo_root=self.repo,
            db_path=db_path,
            execution_backend=FakeSandboxBackend(),
        )
        self.cloud = _ControlHarness(app=self.app, repo=self.repo)
        self.project = self.app.projects.list_projects()["projects"][0]
        self.links_path = self.repo / "project_links.sqlite"
        ProjectLinks(db_path=self.links_path).link(
            repo_root=str(self.repo), project_id=self.project["id"]
        )
        self.proxy = HttpProxyMcpServer(
            config=ProxyConfig(
                repo_root=self.repo,
                daemon_url=None,
                control_url=self.cloud.url,
                project_links_path=self.links_path,
            )
        )
        self.proxy._http_get = self.cloud.http_get  # type: ignore[method-assign]
        self.proxy._http_post = self.cloud.http_post  # type: ignore[method-assign]

    def tearDown(self) -> None:
        self.app.shutdown()
        self.tmp.cleanup()

    def _call(self, name: str, arguments: dict | None = None) -> dict:
        response = self.proxy.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        self.assertNotIn("error", response, response)
        return response["result"]["structuredContent"]

    def test_tools_list_merges_cloud_and_proxy_local_catalogs(self) -> None:
        response = self.proxy.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {tool["name"] for tool in response["result"]["tools"]}

        self.assertIn("claim.create", names)
        self.assertIn("resource.register_file", names)
        self.assertIn("sandbox.get", names)
        self.assertNotIn("project.list", names)
        for tool in response["result"]["tools"]:
            self.assertNotIn("plane", tool)
            self.assertNotIn("project_id", tool["inputSchema"].get("properties", {}))

    def test_control_tool_routes_to_cloud_with_project_id(self) -> None:
        claim = self._call(
            "claim.create",
            {"project_id": "caller_supplied_wrong", "statement": "A control claim."},
        )

        self.assertEqual(claim["project_id"], self.project["id"])
        listed = self._call("claim.list", {"project_id": "caller_supplied_wrong"})
        self.assertIn(claim["id"], {c["id"] for c in listed["claims"]})

    def test_data_tool_reads_local_file_and_submits_observation_to_control(self) -> None:
        (self.repo / "note.txt").write_text("hello from proxy-local data plane\n")

        resource = self._call("resource.register_file", {"path": "note.txt"})

        self.assertEqual(resource["path"], "note.txt")
        self.assertEqual(resource["project_id"], self.project["id"])
        self.assertTrue(resource["current_version"]["content_sha256"])

    def test_aggregate_health_reports_proxy_data_plane_and_cloud(self) -> None:
        health = self._call("sandbox.health")

        self.assertIn("data_plane", health)
        self.assertIn("control_plane", health)
        self.assertTrue(health["data_plane"]["reachable"])
        self.assertEqual(health["data_plane"]["mode"], "proxy")
        self.assertTrue(health["control_plane"]["reachable"])
        self.assertTrue(health["control_plane"]["configured"])

    def test_cloud_outage_blocks_control_submission_but_not_local_validation(self) -> None:
        broken = HttpProxyMcpServer(
            config=ProxyConfig(
                repo_root=self.repo,
                daemon_url=None,
                control_url="http://127.0.0.1:1",
                project_links_path=self.links_path,
            )
        )
        (self.repo / "plan.md").write_text("## Summary\nLocal validation only.\n")
        validation = broken.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "resource.validate",
                    "arguments": {"path": "plan.md", "role": "plan"},
                },
            }
        )
        self.assertNotIn("error", validation)
        self.assertEqual(validation["result"]["structuredContent"]["path"], "plan.md")
        self.assertIn("ok", validation["result"]["structuredContent"])

        down = broken.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "claim.list", "arguments": {}},
            }
        )
        self.assertNotIn("error", down)
        result = down["result"]["structuredContent"]
        self.assertEqual(result["error_code"], "cloud_unreachable")
        self.assertTrue(down["result"].get("isError"))

    def test_split_pull_outputs_is_demoted_with_rsync_guidance(self) -> None:
        result = self._call("sandbox.pull_outputs", {"sandbox_uid": "sbx_1"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "split_mode_pull_outputs_unavailable")
        self.assertIn("rsync -az --itemize-changes", result["rsync"])
        self.assertIn("--no-links --no-devices --no-specials", result["rsync"])
        self.assertIn("object storage", result["error"])


class AggregateMergeTest(unittest.TestCase):
    def test_sandbox_get_merges_proxy_local_experiment_dir_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proxy = HttpProxyMcpServer(
                config=ProxyConfig(
                    repo_root=Path(tmp),
                    daemon_url=None,
                    control_url="http://control.invalid",
                )
            )

            proxy._call_cloud = lambda **_: {
                "experiment_id": "exp_1",
                "sandbox_uid": "sbx_1234567890abcdef",
                "status": "running",
                "ssh": {"host": "example", "port": 22, "user": "root"},
            }
            proxy._call_local_data = lambda **_: {
                "local_dir": f"{tmp}/experiments/sandbox-sbx_12345678",
            }

            merged = proxy._call_aggregate(
                name="sandbox.get", arguments={"experiment_id": "exp_1"}
            )

        self.assertIn("local_experiment_dir", merged)
        for key in ("command", "raw_command", "key_path", "local_dir", "local_sync_dir"):
            self.assertNotIn(key, merged)
        self.assertNotIn("command", merged["ssh"])
        self.assertNotIn("raw_command", merged["ssh"])
        self.assertNotIn("key_path", merged["ssh"])


class ProxyIdentityResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.links_path = self.repo / "links.sqlite"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _proxy(self) -> HttpProxyMcpServer:
        return HttpProxyMcpServer(
            config=ProxyConfig(
                repo_root=self.repo,
                daemon_url=None,
                control_url="http://control.invalid",
                project_links_path=self.links_path,
            )
        )

    def test_resolve_project_id_reads_proxy_link_store(self) -> None:
        links = ProjectLinks(db_path=self.links_path)
        links.link(repo_root=str(self.repo), project_id="proj_cloud_minted")
        proxy = self._proxy()

        self.assertEqual(proxy._resolve_project_id(), "proj_cloud_minted")
        links.link(repo_root=str(self.repo), project_id="proj_relinked")
        self.assertEqual(proxy._resolve_project_id(), "proj_relinked")

    def test_split_proxy_overrides_caller_supplied_project_id(self) -> None:
        proxy = self._proxy()
        proxy._tool_meta = lambda **_: SimpleNamespace(project_scoped=True)  # type: ignore[method-assign]
        proxy._resolve_project_id = lambda: "proj_authoritative"  # type: ignore[method-assign]
        captured: dict = {}

        def _capture_post(**kwargs):  # noqa: ANN003
            captured.update(kwargs.get("payload") or {})
            return {"result": {}}

        proxy._http_post = _capture_post  # type: ignore[method-assign]
        proxy._call_cloud(name="claim.list", arguments={"project_id": "proj_evil"})

        self.assertEqual(captured["arguments"]["project_id"], "proj_authoritative")

    def test_project_current_reports_unlinked_folder_without_cloud_lookup(self) -> None:
        proxy = self._proxy()

        response = proxy.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "project.current", "arguments": {}},
            }
        )

        self.assertNotIn("error", response)
        current = response["result"]["structuredContent"]
        self.assertFalse(current["exists"])
        self.assertIsNone(current["project"])
        self.assertIn("link --project-id", current["hint"])
        self.assertEqual(current["repo_root"], str(self.repo))

    def test_project_current_fetches_linked_cloud_project_by_id(self) -> None:
        ProjectLinks(db_path=self.links_path).link(
            repo_root=str(self.repo), project_id="proj_linked"
        )
        proxy = self._proxy()
        captured: dict = {}

        def _fake_cloud(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {"id": kwargs["arguments"]["project_id"], "name": "Linked"}

        proxy._call_cloud = _fake_cloud  # type: ignore[method-assign]

        response = proxy.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "project.current", "arguments": {}},
            }
        )

        self.assertNotIn("error", response)
        self.assertEqual(captured["name"], "project.get")
        self.assertEqual(captured["arguments"], {"project_id": "proj_linked"})
        current = response["result"]["structuredContent"]
        self.assertTrue(current["exists"])
        self.assertEqual(current["project"]["id"], "proj_linked")
        self.assertEqual(current["project"]["repo_root"], str(self.repo))

    def test_split_proxy_strips_project_id_from_proxy_local_data_calls(self) -> None:
        proxy = self._proxy()
        captured: dict = {}

        class _Executor:
            def call_tool(self, *, name: str, arguments: dict) -> dict:
                captured["name"] = name
                captured["arguments"] = arguments
                return {}

        proxy._local_data_plane = _Executor()  # type: ignore[assignment]
        proxy._call_local_data(
            name="resource.register_file", arguments={"project_id": "proj_evil"}
        )

        self.assertEqual(captured["name"], "resource.register_file")
        self.assertNotIn("project_id", captured["arguments"])


if __name__ == "__main__":
    unittest.main()

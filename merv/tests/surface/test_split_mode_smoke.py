"""Split-mode smoke tests with the stateless MCP proxy as the local data plane."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from tests.support.brain import TestBrain
from merv.brain.surface.control.control_runtime import ControlTaskChannel
from merv.brain.sandbox.execution.backends.fake import FakeSandboxBackend
from merv.brain.surface.transport.http_api import create_fastapi_app
from merv.proxy.errors import UpstreamError
from merv.proxy.project_links import ProjectLinks
from merv.proxy.proxy import HttpProxyMcpServer, ProxyConfig


VALID_PUBLIC_KEY = "ssh-ed25519 " + ("A" * 48) + " caller@test"


class _ControlHarness:
    def __init__(self, *, app: TestBrain) -> None:
        self.url = "http://control.test"
        self.client = TestClient(
            create_fastapi_app(app=app.http), raise_server_exceptions=False
        )

    def http_get(self, *, url: str, is_cloud: bool) -> dict:  # noqa: ARG002
        response = self.client.get(urlsplit(url).path)
        response.raise_for_status()
        return response.json()

    def http_post(
        self, *, url: str, payload: dict, is_cloud: bool, timeout=None
    ) -> dict:  # noqa: ANN001, ARG002
        response = self.client.post(urlsplit(url).path, json=payload)
        if response.status_code >= 400:
            body = response.json()
            raise UpstreamError(
                str(body.get("detail") or response.text),
                error_code=str(body.get("error_code") or "upstream_http_error"),
                details={
                    key: value
                    for key, value in body.items()
                    if key not in {"detail", "error_code"}
                },
            )
        return response.json()


class ProxyLocalDataPlaneSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.project_id = "proj_split"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_proxy_local_catalog_advertises_only_enriched_control_tools(
        self,
    ) -> None:
        proxy = HttpProxyMcpServer(
            config=ProxyConfig(
                repo_root=self.repo,
                control_url="http://control.invalid",
            )
        )
        local_names = {tool["name"] for tool in proxy._local_tool_catalog()}

        self.assertIn("sandbox.get", local_names)
        self.assertNotIn("sandbox.request", local_names)
        self.assertNotIn("sandbox.attach", local_names)
        self.assertNotIn("sandbox.pull_outputs", local_names)
        self.assertNotIn("claim.create", local_names)

    def test_control_task_teardown_noops_without_conn_file(self) -> None:
        channel = ControlTaskChannel()

        self.assertIsNone(
            channel.submit(
                task_type="teardown",
                payload={"experiment_id": "exp_1", "sandbox_uid": "sbx_1"},
            )
        )

    # feed.post is a control tool since the no-dataplane transition (Phase D.1):
    # the proxy forwards it to /mcp unchanged and carries no feed media handler,
    # so the former local-read smoke tests (which drove /api/data-plane/feed/*)
    # are gone. feed-media upload is covered by tests/workflow/test_feed.py's
    # token-PUT flow.


class PrivateSplitProxyTest(unittest.TestCase):
    def test_split_proxy_sends_project_id_not_repo_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            links_path = repo / "links.sqlite"
            ProjectLinks(db_path=links_path).link(
                repo_root=str(repo), project_id="proj_1"
            )
            proxy = HttpProxyMcpServer(
                config=ProxyConfig(
                    repo_root=repo,
                    control_url="http://control.invalid",
                    project_links_path=links_path,
                )
            )
            captured: dict = {}

            def capture_post(**kwargs):  # noqa: ANN003
                captured.update(kwargs["payload"])
                return {"result": {"claims": []}}

            proxy._http.post = capture_post  # type: ignore[method-assign]
            proxy._tool_meta = lambda **_: type(  # type: ignore[method-assign]
                "Meta", (), {"project_scoped": True, "plane": "control"}
            )()

            proxy._call_cloud(name="claim.list", arguments={"project_id": "wrong"})

        self.assertEqual(captured["arguments"], {"project_id": "proj_1"})
        self.assertNotIn("context", captured)


class SplitModeSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.app = TestBrain(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            execution_backend=FakeSandboxBackend(),
        )
        self.project = self.app.projects.list_projects()["projects"][0]
        self.links_path = self.repo / "links.sqlite"
        ProjectLinks(db_path=self.links_path).link(
            repo_root=str(self.repo), project_id=self.project["id"]
        )
        self.control = _ControlHarness(app=self.app)
        self.proxy = HttpProxyMcpServer(
            config=ProxyConfig(
                repo_root=self.repo,
                control_url=self.control.url,
                project_links_path=self.links_path,
            )
        )
        self.proxy._http.get = self.control.http_get  # type: ignore[method-assign]
        self.proxy._http.post = self.control.http_post  # type: ignore[method-assign]

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

    def test_standalone_sandbox_request_through_proxy_with_caller_key(self) -> None:
        result = self._call("sandbox.request", {"public_key": VALID_PUBLIC_KEY})

        self.assertEqual(result["project_id"], self.project["id"])
        self.assertEqual(result["public_key_source"], "caller")
        self.assertNotIn("key_path", result.get("ssh", {}))

    def test_sandbox_request_without_public_key_returns_tool_error(self) -> None:
        response = self.proxy.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "sandbox.request", "arguments": {}},
            }
        )

        self.assertEqual(response["error"]["data"]["error_code"], "validation_error")
        self.assertIn("public_key is required", response["error"]["message"])

    def test_sandbox_request_rejects_private_key_material(self) -> None:
        response = self.proxy.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "sandbox.request",
                    "arguments": {"public_key": "-----BEGIN OPENSSH PRIVATE KEY-----"},
                },
            }
        )

        self.assertEqual(response["error"]["data"]["error_code"], "validation_error")
        self.assertIn("private-key", response["error"]["message"].lower())

    def test_full_loop_through_split_proxy_artifact_submission(self) -> None:
        claim = self._call("claim.create", {"statement": "Split proxy can ship files."})
        exp = self._call(
            "experiment.create",
            {
                "name": "split-proxy-loop",
                "intent": "Exercise artifact submission through the split proxy.",
                "tested_claim_ids": [claim["id"]],
            },
        )
        pending = self._call(
            "artifact.submit",
            {
                "target_type": "experiment",
                "target_id": exp["id"],
                "role": "plan",
                "path": "experiments/split-proxy-loop/plan.md",
            },
        )
        self.assertIn("curl -sf -T", pending["run"])
        token = pending["run"].rsplit("/", 1)[-1].rstrip("'")
        uploaded = self.app.upload_artifact_bytes(
            token=token,
            data=(
                "## Summary\nPlan.\n\n## Objective & hypothesis\nGoal.\n\n"
                "## Evaluation\nMetric.\n"
            ).encode(),
        )
        current = self._call("project", {"action": "current"})

        self.assertEqual(uploaded["artifact_id"], pending["artifact_id"])
        state = self._call(
            "experiment.get_state", {"experiment_id": exp["id"]}
        )
        roles = {
            item["role"]
            for item in state["current_attempt_artifacts"]
        }
        self.assertIn("plan", roles)
        self.assertTrue(current["exists"])
        self.assertEqual(current["project"]["id"], self.project["id"])


if __name__ == "__main__":
    unittest.main()

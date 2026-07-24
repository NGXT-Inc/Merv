"""Shipped client adapters all use the same bearer-authenticated HTTP MCP."""

from __future__ import annotations

import json
import re
import tomllib
import unittest

from merv.brain import __version__ as BACKEND_VERSION
from tests.paths import PLUGIN_ROOT


HOSTED_MCP_URL = "https://experiments.rapidreview.io/mcp"
AUTHORIZATION = "Bearer ${MERV_MCP_KEY}"


class HttpMcpManifestTest(unittest.TestCase):
    def test_generic_codex_and_cursor_manifests_match(self) -> None:
        for name in (".mcp.json", ".mcp.codex.json", "mcp.json"):
            with self.subTest(manifest=name):
                config = json.loads((PLUGIN_ROOT / name).read_text())
                server = config["mcpServers"]["merv"]
                self.assertEqual(server["type"], "http")
                self.assertEqual(server["url"], HOSTED_MCP_URL)
                self.assertEqual(
                    server["headers"]["Authorization"],
                    AUTHORIZATION,
                )
                serialized = json.dumps(server)
                self.assertNotIn("mk_", serialized)
                self.assertNotIn("merv-mcp", serialized)

    def test_gemini_uses_the_same_http_endpoint_and_key(self) -> None:
        manifest = json.loads((PLUGIN_ROOT / "gemini-extension.json").read_text())
        self.assertEqual(manifest["name"], "merv")
        self.assertEqual(manifest["version"], BACKEND_VERSION)
        server = manifest["mcpServers"]["merv"]
        self.assertEqual(server["httpUrl"], HOSTED_MCP_URL)
        self.assertEqual(server["headers"]["Authorization"], AUTHORIZATION)

    def test_opencode_example_uses_environment_key_indirection(self) -> None:
        config = json.loads(
            (PLUGIN_ROOT / "clients" / "opencode" / "opencode.json.example").read_text()
        )
        server = config["mcp"]["merv"]
        self.assertEqual(server["type"], "remote")
        self.assertEqual(server["url"], HOSTED_MCP_URL)
        self.assertEqual(
            server["headers"]["Authorization"],
            "Bearer {env:MERV_MCP_KEY}",
        )

    def test_plugin_manifests_keep_package_identity(self) -> None:
        for directory in (".claude-plugin", ".cursor-plugin"):
            manifest = json.loads(
                (PLUGIN_ROOT / directory / "plugin.json").read_text()
            )
            self.assertEqual(manifest["name"], "merv")
            self.assertEqual(manifest["version"], BACKEND_VERSION)
        codex = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text()
        )
        self.assertEqual(codex["name"], "merv")
        self.assertEqual(codex["mcpServers"], "./.mcp.codex.json")

    def test_release_version_lockstep(self) -> None:
        # One release number everywhere: a UI or package left behind produces
        # a permanent false "reload this UI" compat banner against /api/meta.
        pyproject = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text())
        self.assertEqual(pyproject["project"]["version"], BACKEND_VERSION)
        api_js = (PLUGIN_ROOT.parent / "research_state_ui" / "src" / "api.js").read_text()
        match = re.search(r"CLIENT_VERSION = '([^']+)'", api_js)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), BACKEND_VERSION)
        codex = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())
        self.assertTrue(codex["version"].startswith(BACKEND_VERSION))


if __name__ == "__main__":
    unittest.main()

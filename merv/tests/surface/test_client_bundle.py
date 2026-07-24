"""The slim client bundle ships what a thin HTTP-MCP client needs — and nothing
heavy. Guards scripts/build_client_bundle.py so the bundle definition cannot rot
(a new skill/agent is picked up; the backend/tests can never leak in)."""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.paths import PLUGIN_ROOT


def _imports_merv_brain(source: str) -> bool:
    """True if the module reaches merv.brain by any import form (absolute,
    `from merv import brain`, relative `from ..brain import x` / `from .. import
    brain`, or `__import__("merv.brain")`) — but not a prose mention."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[:2] == ["merv", "brain"] for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module_parts = (node.module or "").split(".")
            names = {a.name for a in node.names}
            if module_parts[:2] == ["merv", "brain"]:
                return True
            if node.level == 0 and module_parts == ["merv"] and "brain" in names:
                return True
            # Relative import climbing into a sibling/parent `brain`.
            if node.level > 0 and ("brain" in module_parts or "brain" in names):
                return True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "__import__" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value.split(".")[:2] == ["merv", "brain"]:
                        return True
    return False


def _string_values(obj) -> list[str]:
    """Every string value in a nested JSON structure."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _string_values(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _string_values(v)]
    return []

_spec = importlib.util.spec_from_file_location(
    "build_client_bundle", PLUGIN_ROOT / "scripts" / "build_client_bundle.py"
)
build_client_bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_client_bundle)


class ClientBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = build_client_bundle.manifest()

    def test_every_included_path_resolves(self) -> None:
        # manifest() raises SystemExit on a missing INCLUDE entry; reaching here
        # means all resolved. Assert it produced a non-trivial, deduped list.
        self.assertGreater(len(self.manifest), 20)
        self.assertEqual(len(self.manifest), len(set(self.manifest)))

    def test_backend_and_tests_never_ship(self) -> None:
        for rel in self.manifest:
            for bad in ("src/merv/brain/", "tests/", "deploy/", "merv-http"):
                self.assertNotIn(bad, rel, f"{rel} leaks {bad} into the client bundle")

    def test_client_essentials_present(self) -> None:
        required = {
            ".claude-plugin/plugin.json",
            ".codex-plugin/plugin.json",
            ".cursor-plugin/plugin.json",
            ".mcp.json",
            ".mcp.codex.json",
            "mcp.json",
            "gemini-extension.json",
            "GEMINI.md",
            "AGENTS.md",
            "bin/merv-client",
            "src/merv/client/cli.py",
            # Codex manifest's composerIcon must resolve inside the bundle.
            "assets/icon.svg",
        }
        missing = required - set(self.manifest)
        self.assertFalse(missing, f"client essentials missing from bundle: {missing}")

    def test_manifest_referenced_assets_resolve(self) -> None:
        # Any ./assets or ./file path a bundled manifest points at must be in the
        # bundle, so the installed plugin has no dangling reference.
        bundled = set(self.manifest)
        for mrel in (".codex-plugin/plugin.json", "gemini-extension.json"):
            data = json.loads((PLUGIN_ROOT / mrel).read_text())
            for value in _string_values(data):
                if not value.startswith("./") or "/" not in value[2:]:
                    continue
                ref = value[2:]
                if ref.endswith("/"):  # directory reference — some file under it
                    ok = any(b.startswith(ref) for b in bundled)
                else:  # file reference — exact
                    ok = ref in bundled
                self.assertTrue(ok, f"{mrel} references unbundled {ref}")

    def test_all_skills_and_reviewer_agents_included(self) -> None:
        # Every skill directory and reviewer agent on disk must reach the bundle,
        # so adding one and forgetting the bundle fails here.
        skills_on_disk = {p.parent.name for p in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")}
        skills_in_bundle = {
            rel.split("/")[1] for rel in self.manifest if rel.startswith("skills/")
        }
        self.assertEqual(skills_on_disk, skills_in_bundle)

        agents_on_disk = {p.name for p in (PLUGIN_ROOT / "agents").glob("*.md")}
        agents_in_bundle = {
            rel.split("/", 1)[1] for rel in self.manifest if rel.startswith("agents/")
        }
        self.assertEqual(agents_on_disk, agents_in_bundle)

    def test_built_bundle_installs_and_cli_runs(self) -> None:
        # Build the real bundle and prove the output closure: no backend/tests,
        # the Codex icon resolves, and merv-client runs from the bundle's own src
        # (which only works if the client+shared closure is complete and self
        # contained).
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plugin"
            build_client_bundle.build(out)
            self.assertTrue((out / "assets" / "icon.svg").is_file())
            self.assertTrue((out / "skills").is_dir() and (out / "agents").is_dir())
            leaked = [
                p for p in out.rglob("*")
                if p.is_file() and ("brain" in p.parts or "tests" in p.parts)
            ]
            self.assertEqual(leaked, [], f"backend/tests leaked into build: {leaked}")

            result = subprocess.run(
                [sys.executable, "-m", "merv.client.cli", "env"],
                cwd=out,
                env={"PYTHONPATH": str(out / "src"), "PATH": ""},
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            snippet = json.loads(result.stdout)
            self.assertEqual(
                snippet["mcpServers"]["merv"]["type"], "http", result.stdout
            )

    def test_slim_src_is_self_contained(self) -> None:
        # The bundled src must be exactly merv.client + merv.shared (+ __init__),
        # and must not import merv.brain — the property that lets it run without
        # the backend present.
        src_files = [Path(rel) for rel in self.manifest if rel.startswith("src/")]
        self.assertTrue(src_files)
        for rel in src_files:
            self.assertNotIn("brain", rel.parts)
            text = (PLUGIN_ROOT / rel).read_text(encoding="utf-8")
            self.assertFalse(_imports_merv_brain(text), f"{rel} imports merv.brain")


if __name__ == "__main__":
    unittest.main()

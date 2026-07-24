"""The slim client bundle ships what a thin HTTP-MCP client needs — and nothing
heavy. Guards scripts/build_client_bundle.py so the bundle definition cannot rot
(a new skill/agent is picked up; the backend/tests can never leak in)."""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

from tests.paths import PLUGIN_ROOT


def _imports_merv_brain(source: str) -> bool:
    """True if the module actually imports from merv.brain (not a prose mention)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[:2] == ["merv", "brain"] for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[:2] == ["merv", "brain"]:
                return True
    return False

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
            "mcp.json",
            "gemini-extension.json",
            "bin/merv-client",
            "src/merv/client/cli.py",
        }
        missing = required - set(self.manifest)
        self.assertFalse(missing, f"client essentials missing from bundle: {missing}")

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

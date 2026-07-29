"""Durable architecture rules for the lean Sandbox control plane."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from merv.brain.sandbox import SandboxEngine


ROOT = Path(__file__).parents[2] / "src" / "merv" / "brain"
SANDBOX = ROOT / "sandbox"
GUIDE = SANDBOX / "sandbox.md"
MAINTENANCE_HEADER = (
    "# If you update this file, you must consult sandbox.md to see whether "
    "sandbox.md needs to be updated. sandbox.md must not exceed 100 lines."
)


class SandboxArchitectureTest(unittest.TestCase):
    def test_module_guide_is_bounded_and_named_by_every_source_file(self) -> None:
        self.assertLessEqual(
            len(GUIDE.read_text(encoding="utf-8").splitlines()),
            100,
        )
        missing = [
            str(path.relative_to(SANDBOX))
            for path in sorted(SANDBOX.rglob("*.py"))
            if path.read_text(encoding="utf-8").splitlines()[0]
            != MAINTENANCE_HEADER
        ]
        self.assertEqual(missing, [])

    def test_engine_is_the_only_public_control_object(self) -> None:
        self.assertEqual(SandboxEngine.__module__, "merv.brain.sandbox.core")
        namespace: dict[str, object] = {}
        exec("from merv.brain.sandbox import *", namespace)
        self.assertEqual(
            {
                name
                for name in namespace
                if not name.startswith("__")
            },
            {
                "SandboxBackend",
                "SandboxEngine",
            },
        )

    def test_production_never_reaches_through_the_engine(self) -> None:
        offenders: list[str] = []
        forbidden = {
            "repository",
            "observation",
            "runs_ledger",
            "lifecycle",
            "provisioner",
            "metrics",
            "daemons",
            "quotas",
            "store",
            "backend",
            "mgmt_keys",
            "transcript_cache",
            "runtime",
            "queries",
        }
        for path in ROOT.rglob("*.py"):
            if path.is_relative_to(SANDBOX):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in forbidden
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "sandboxes"
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_production_imports_sandbox_only_through_package_root(self) -> None:
        offenders: list[str] = []
        bootstrap_seams = {
            (
                "surface/composition/control_mode.py",
                "merv.brain.sandbox.adapters",
            ),
            (
                "surface/composition/control_mode.py",
                "merv.brain.sandbox.keys",
            ),
        }
        root_package = ("merv", "brain")
        for path in ROOT.rglob("*.py"):
            if path.is_relative_to(SANDBOX):
                continue
            package = (*root_package, *path.relative_to(ROOT).parent.parts)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                if node.level:
                    keep = len(package) - (node.level - 1)
                    target = (*package[:keep], *node.module.split("."))
                else:
                    target = tuple(node.module.split("."))
                if target[:3] == ("merv", "brain", "sandbox") and len(target) > 3:
                    boundary = (
                        str(path.relative_to(ROOT)),
                        ".".join(target),
                    )
                    if boundary not in bootstrap_seams:
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_core_contains_business_flow_not_io_implementation(self) -> None:
        source = (SANDBOX / "core.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("subprocess", source)
        self.assertNotIn("httpx", source)
        self.assertFalse(any("adapters." in name for name in imports))
        for sql in ("SELECT ", "INSERT INTO ", "UPDATE ", "DELETE FROM "):
            self.assertNotIn(sql, source)
        lifecycle = (SANDBOX / "lifecycle.py").read_text(encoding="utf-8")
        self.assertNotIn("from .core import", lifecycle)
        self.assertNotIn("from .lifecycle import", source[source.index("class SandboxEngine"):])

    def test_storage_does_not_import_control_or_provider_code(self) -> None:
        tree = ast.parse((SANDBOX / "storage.py").read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertFalse(
            any(
                name.endswith(("core", "lifecycle", "sandbox_backend"))
                for name in imports
            )
        )
        observation = (SANDBOX / "observation.py").read_text(encoding="utf-8")
        self.assertIn("AND finished_event_emitted = 0", observation)
        storage = (SANDBOX / "storage.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            storage.count("AND status = 'provisioning'"),
            2,
        )


if __name__ == "__main__":
    unittest.main()

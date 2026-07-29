"""Durable architecture rules for the lean Sandbox control plane."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from merv.brain.sandbox.core import SandboxEngine


ROOT = Path(__file__).parents[2] / "src" / "merv" / "brain"
SANDBOX = ROOT / "sandbox"


class SandboxArchitectureTest(unittest.TestCase):
    def test_engine_is_the_only_public_control_object(self) -> None:
        self.assertEqual(SandboxEngine.__module__, "merv.brain.sandbox.core")
        for obsolete in (
            "facade.py",
            "runtime.py",
            "queries.py",
            "sandbox_views.py",
            "lifecycle_reducer.py",
            "sandbox_metrics.py",
            "sandbox_runs.py",
            "transcript_cache.py",
        ):
            self.assertFalse((SANDBOX / obsolete).exists(), obsolete)

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
        self.assertFalse(any("execution.backends." in name for name in imports))
        for sql in ("SELECT ", "INSERT INTO ", "UPDATE ", "DELETE FROM "):
            self.assertNotIn(sql, source)

    def test_storage_does_not_import_control_or_provider_code(self) -> None:
        tree = ast.parse((SANDBOX / "storage.py").read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertFalse(
            any(
                name.endswith(("core", "sandbox_lifecycle", "sandbox_backend"))
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

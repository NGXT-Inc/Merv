"""Fail-closed size and compatibility-wrapper ratchets for this migration."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BRAIN = ROOT / "src" / "merv" / "brain"
BASELINE_BRAIN_LOC = 39_924
MAX_BRAIN_LOC = 40_860
BASELINE_SURFACE_ORCHESTRATION_LOC = 1_022


class ApplicationArchitectureBudgetTest(unittest.TestCase):
    def test_adversarially_reratified_brain_loc_ceiling(self) -> None:
        current = sum(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in BRAIN.rglob("*.py")
        )
        self.assertLessEqual(current, MAX_BRAIN_LOC)
        self.assertEqual(MAX_BRAIN_LOC - BASELINE_BRAIN_LOC, 936)

    def test_surface_orchestration_shrank_by_at_least_120_lines(self) -> None:
        current = len(
            (BRAIN / "surface/tools/tool_handlers.py")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertLessEqual(current, BASELINE_SURFACE_ORCHESTRATION_LOC - 120)
        self.assertFalse((BRAIN / "surface/tools/exhibits.py").exists())

    def test_mlflow_compatibility_wrapper_is_import_only(self) -> None:
        path = BRAIN / "mlflow/exhibit.py"
        source = path.read_text(encoding="utf-8")
        self.assertLessEqual(len(source.splitlines()), 15)
        tree = ast.parse(source, filename=str(path))
        self.assertTrue(
            all(
                isinstance(node, ast.ImportFrom)
                or (
                    isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                )
                for node in tree.body
            ),
            "compatibility wrapper may contain only its docstring and imports",
        )


if __name__ == "__main__":
    unittest.main()

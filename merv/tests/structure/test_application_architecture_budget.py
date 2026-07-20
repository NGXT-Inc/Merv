"""Fail-closed size and compatibility-wrapper ratchets for this migration."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BRAIN = ROOT / "src" / "merv" / "brain"
BASELINE_BRAIN_LOC = 39_924
PRE_TRACKING_SLICE_LOC = 40_850
MAX_BRAIN_LOC = 41_000
BASELINE_SURFACE_ORCHESTRATION_LOC = 1_022
PRE_TRACKING_SURFACE_LOC = 549
MAX_SURFACE_ORCHESTRATION_LOC = 232


class ApplicationArchitectureBudgetTest(unittest.TestCase):
    def test_brain_loc_ceiling(self) -> None:
        current = sum(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in BRAIN.rglob("*.py")
        )
        self.assertLessEqual(current, MAX_BRAIN_LOC)
        self.assertEqual(MAX_BRAIN_LOC - PRE_TRACKING_SLICE_LOC, 150)
        self.assertEqual(MAX_BRAIN_LOC - BASELINE_BRAIN_LOC, 1_076)

    def test_surface_orchestration_shrank_by_at_least_120_lines(self) -> None:
        current = len(
            (BRAIN / "surface/tools/tool_handlers.py")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertLessEqual(current, MAX_SURFACE_ORCHESTRATION_LOC)
        self.assertEqual(PRE_TRACKING_SURFACE_LOC - MAX_SURFACE_ORCHESTRATION_LOC, 317)
        self.assertEqual(
            BASELINE_SURFACE_ORCHESTRATION_LOC - MAX_SURFACE_ORCHESTRATION_LOC,
            790,
        )
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

    def test_review_and_reaction_orchestration_stays_out_of_surface(self) -> None:
        handlers = (BRAIN / "surface/tools/tool_handlers.py").read_text(encoding="utf-8")
        transition = (BRAIN / "application/experiments/transition.py").read_text(
            encoding="utf-8"
        )
        composition = (BRAIN / "surface/control/control_app.py").read_text(
            encoding="utf-8"
        )
        for removed in (
            "def review_status_agent",
            "experiment_review_verdict",
            "build_local_tool_handlers",
        ):
            self.assertNotIn(removed, handlers)
        self.assertIn('"review.status": review_status.execute', handlers)
        self.assertNotIn("EventDispatcher()", transition)
        self.assertNotIn(".register(", transition)
        self.assertIn("self.reaction_registry = EventDispatcher()", composition)


if __name__ == "__main__":
    unittest.main()

"""Fail-closed compatibility-wrapper and ownership ratchets for this migration."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BRAIN = ROOT / "src" / "merv" / "brain"


class ApplicationArchitectureBudgetTest(unittest.TestCase):
    def test_removed_compatibility_wrappers_stay_gone(self) -> None:
        self.assertFalse((BRAIN / "surface/tools/exhibits.py").exists())
        self.assertFalse((BRAIN / "mlflow/exhibit.py").exists())

    def test_review_and_reaction_orchestration_stays_out_of_surface(self) -> None:
        handlers = (BRAIN / "surface/tools/tool_handlers.py").read_text(encoding="utf-8")
        transition = (BRAIN / "application/experiments/transition.py").read_text(
            encoding="utf-8"
        )
        tracking = (BRAIN / "application/experiments/tracking.py").read_text(
            encoding="utf-8"
        )
        reactions = (BRAIN / "application/experiments/reactions.py").read_text(
            encoding="utf-8"
        )
        views = (BRAIN / "surface/transport/api/views.py").read_text(
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
        manifest = (BRAIN / "surface/tools/contracts.py").read_text(encoding="utf-8")
        self.assertIn('handler_identity="review_status.execute"', manifest)
        self.assertIn("for name, tool in TOOL_MANIFEST.items()", handlers)
        for application_decision in (
            "slim_experiment_state",
            "ValidationError",
            "def project_control",
            "def resource_find",
            "def storage_find",
            "def storage_object",
        ):
            self.assertNotIn(application_decision, handlers)
        for use_case in (transition, tracking):
            self.assertNotIn("EventDispatcher()", use_case)
            self.assertNotIn(".register(", use_case)
        self.assertIn("EXPERIMENT_REACTION_CATALOG", reactions)
        self.assertIn("registry.bind_catalog(", reactions)
        self.assertNotIn("registry.register(", reactions)
        self.assertIn("self.reaction_registry = EventDispatcher()", composition)
        self.assertEqual(composition.count("dispatcher=self.reaction_registry"), 3)
        self.assertNotIn("self.app.mlflow_tracking", views)
        self.assertNotIn("tracking_visible_for_status", views)
        experiment_routes = (BRAIN / "surface/transport/api/experiments.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("present(detail(", experiment_routes)
        self.assertNotIn("experiment_detail", views)

    def test_tool_operations_receive_public_component_contracts(self) -> None:
        commands = (BRAIN / "application/tool_commands.py").read_text(encoding="utf-8")
        composition = (BRAIN / "surface/control/control_app.py").read_text(
            encoding="utf-8"
        )
        for raw_service in ("projects: Any", "claims: Any", "resources: Any", "storage: Any"):
            self.assertNotIn(raw_service, commands)
        for binding in ("projects=core.projects", "claims=core.claims", "resources=core.resources"):
            self.assertIn(binding, composition)


if __name__ == "__main__":
    unittest.main()

"""Project-scoped activity feed.

The Activity page is per-project, not a cross-project firehose. In shared
(router) mode each project keeps its own activity log, so a scoped request must
return only that project's events — and its own summary counts — rather than
the merged-and-truncated view that starved a quiet project of rows.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.execution.backends.fake import FakeSandboxBackend
from backend.daemon.project_router import ProjectRouter


def _backend_factory(_repo: Path) -> FakeSandboxBackend:
    return FakeSandboxBackend()


class ActivityScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.router = ProjectRouter(
            registry_db_path=self.root / "registry.sqlite",
            execution_backend_factory=_backend_factory,
        )
        self.a = self.router.create_project(repo_root=self.root / "alpha", name="Alpha")
        self.b = self.router.create_project(repo_root=self.root / "beta", name="Beta")
        # Seed one distinct tool.call into each project's own activity log.
        self.router.app_for_project(self.a["id"]).activity.tool_ok(
            source="mcp", tool="experiment.create",
            arguments={"project_id": self.a["id"]}, duration_ms=1, result={"ok": True},
        )
        self.router.app_for_project(self.b["id"]).activity.tool_ok(
            source="mcp", tool="experiment.create",
            arguments={"project_id": self.b["id"]}, duration_ms=1, result={"ok": True},
        )

    def tearDown(self) -> None:
        self.router.shutdown()
        self.tmp.cleanup()

    def test_scoped_request_returns_only_that_project(self) -> None:
        scoped = self.router.activity_recent(limit=50, project_id=self.a["id"])
        self.assertTrue(scoped["events"], "expected the scoped project's own events")
        self.assertEqual({e.get("project_id") for e in scoped["events"]}, {self.a["id"]})
        # Scoped summary is the project's own (source/status counts), not the
        # cross-project {"workspaces": [...]} wrapper.
        self.assertIn("source_counts", scoped["summary"])
        self.assertNotIn("workspaces", scoped["summary"])

    def test_unknown_project_is_empty(self) -> None:
        result = self.router.activity_recent(limit=50, project_id="proj_does_not_exist")
        self.assertEqual(result["events"], [])

    def test_unscoped_request_still_merges_all_projects(self) -> None:
        merged = self.router.activity_recent(limit=50)
        pids = {e.get("project_id") for e in merged["events"]}
        self.assertEqual(pids, {self.a["id"], self.b["id"]})
        self.assertIn("workspaces", merged["summary"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app import ResearchPluginApp
from backend.utils import ValidationError


class ProjectToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.app = ResearchPluginApp(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def call(self, tool: str, **kwargs):
        return self.app.call_tool(tool, kwargs)

    def test_active_project_view_omits_hard_stop_fields(self) -> None:
        project = self.call("project.create", name="Alpha")
        self.assertNotIn("hard_stop_reflection_id", project)
        self.assertNotIn("hard_stop_rationale", project)
        self.assertNotIn("stopped_at", project)

        fetched = self.call("project.get", project_id=project["id"])
        self.assertNotIn("hard_stop_reflection_id", fetched)
        self.assertNotIn("hard_stop_rationale", fetched)
        self.assertNotIn("stopped_at", fetched)

    def test_stopped_project_view_includes_hard_stop_fields(self) -> None:
        project = self.call("project.create", name="Alpha")
        with self.app.store.transaction() as conn:
            conn.execute(
                """
                UPDATE projects
                SET status = 'stopped',
                    hard_stop_reflection_id = 'syn_test',
                    hard_stop_rationale = 'No viable directions remain.',
                    stopped_at = '2026-01-01T00:00:00Z'
                WHERE id = ?
                """,
                (project["id"],),
            )

        stopped = self.call("project.get", project_id=project["id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["hard_stop_reflection_id"], "syn_test")
        self.assertEqual(stopped["hard_stop_rationale"], "No viable directions remain.")
        self.assertEqual(stopped["stopped_at"], "2026-01-01T00:00:00Z")

    def test_project_name_must_be_at_least_three_chars_on_create_and_update(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            self.call("project.create", name="ab")
        self.assertIn("at least 3", str(ctx.exception))

        project = self.call("project.create", name="Alpha")
        with self.assertRaises(ValidationError) as empty_ctx:
            self.call("project.update", project_id=project["id"], name=" ")
        self.assertIn("name is required", str(empty_ctx.exception))

        with self.assertRaises(ValidationError) as short_ctx:
            self.call("project.update", project_id=project["id"], name="xy")
        self.assertIn("at least 3", str(short_ctx.exception))

        updated = self.call("project.update", project_id=project["id"], name="Beta")
        self.assertEqual(updated["name"], "Beta")


if __name__ == "__main__":
    unittest.main()

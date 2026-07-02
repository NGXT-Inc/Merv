from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app import ResearchPluginApp
from backend.execution.backends.fake import FakeSandboxBackend
from backend.utils import ValidationError


class ResultsMergeToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.app = ResearchPluginApp(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            execution_backend=FakeSandboxBackend(),
        )
        self.project_id = self.app.call_tool("project.create", {"name": "Merge TSV"})[
            "id"
        ]

    def tearDown(self) -> None:
        self.app.shutdown()
        self.tmp.cleanup()

    def test_merge_tool_appends_rows_and_refuses_conflicts(self) -> None:
        (self.repo / "results.tsv").write_text(
            "row_id\tmetric\tvalue\n"
            "a\taccuracy\t0.70\n",
            encoding="utf-8",
        )
        (self.repo / "incoming.tsv").write_text(
            "row_id\tmetric\tvalue\n"
            "b\taccuracy\t0.72\n",
            encoding="utf-8",
        )

        result = self.app.call_tool(
            "results.merge_tsv",
            {
                "project_id": self.project_id,
                "source_path": "incoming.tsv",
                "target_path": "results.tsv",
            },
        )

        self.assertEqual(result["inserted_rows"], 1)
        self.assertEqual(result["target_rows_after"], 2)

        before_conflict = (self.repo / "results.tsv").read_text(encoding="utf-8")
        (self.repo / "incoming.tsv").write_text(
            "row_id\tmetric\tvalue\n"
            "b\taccuracy\t0.73\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValidationError):
            self.app.call_tool(
                "results.merge_tsv",
                {
                    "project_id": self.project_id,
                    "source_path": "incoming.tsv",
                    "target_path": "results.tsv",
                },
            )
        self.assertEqual(
            (self.repo / "results.tsv").read_text(encoding="utf-8"),
            before_conflict,
        )


if __name__ == "__main__":
    unittest.main()

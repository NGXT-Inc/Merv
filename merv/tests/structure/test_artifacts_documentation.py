"""Durable maintenance contract for the Artifacts module guide."""

from __future__ import annotations

import unittest

from tests.paths import ARTIFACTS_ROOT


GUIDE = ARTIFACTS_ROOT / "artifacts.md"
MAINTENANCE_HEADER = (
    "# If you update this file, you must consult artifacts.md to see whether "
    "artifacts.md needs to be updated. artifacts.md must not exceed 100 lines."
)


class ArtifactsDocumentationTests(unittest.TestCase):
    def test_guide_stays_dense(self) -> None:
        self.assertTrue(GUIDE.is_file(), "Artifacts must retain artifacts.md")
        line_count = len(GUIDE.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(
            line_count,
            100,
            f"{GUIDE} has {line_count} lines; keep the module guide at most 100",
        )

    def test_every_module_source_points_to_the_guide(self) -> None:
        sources = sorted(ARTIFACTS_ROOT.glob("*.py"))
        self.assertTrue(sources, "Artifacts module must contain Python sources")
        for path in sources:
            with self.subTest(path=path.name):
                first_line = path.read_text(encoding="utf-8").splitlines()[0]
                self.assertEqual(first_line, MAINTENANCE_HEADER)

"""Durable maintenance contract for the Research Core module guide."""

from __future__ import annotations

import unittest

from tests.paths import RESEARCH_CORE_ROOT


GUIDE = RESEARCH_CORE_ROOT / "research_core.md"
MAINTENANCE_HEADER = (
    "# If you update this file, you must consult research_core.md to see whether "
    "research_core.md needs to be updated. research_core.md must not exceed 100 lines."
)


class ResearchCoreDocumentationTests(unittest.TestCase):
    def test_guide_stays_dense(self) -> None:
        self.assertTrue(GUIDE.is_file(), "Research Core must retain research_core.md")
        line_count = len(GUIDE.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(
            line_count,
            100,
            f"{GUIDE} has {line_count} lines; keep the module guide at most 100",
        )

    def test_every_module_source_points_to_the_guide(self) -> None:
        sources = sorted(RESEARCH_CORE_ROOT.glob("*.py"))
        self.assertTrue(sources, "Research Core must contain Python sources")
        for path in sources:
            with self.subTest(path=path.name):
                first_line = path.read_text(encoding="utf-8").splitlines()[0]
                self.assertEqual(first_line, MAINTENANCE_HEADER)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""Durable maintenance contract for the Object Storage module guide."""

from __future__ import annotations

import unittest

from tests.paths import OBJECT_STORAGE_ROOT


GUIDE = OBJECT_STORAGE_ROOT / "object_storage.md"
MAINTENANCE_HEADER = (
    "# If you update this file, you must consult object_storage.md to see whether "
    "object_storage.md needs to be updated. object_storage.md must not exceed 100 lines."
)


class ObjectStorageDocumentationTests(unittest.TestCase):
    def test_guide_stays_dense(self) -> None:
        self.assertTrue(
            GUIDE.is_file(), "Object Storage must retain object_storage.md"
        )
        line_count = len(GUIDE.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(
            line_count,
            100,
            f"{GUIDE} has {line_count} lines; keep the module guide at most 100",
        )

    def test_every_module_source_points_to_the_guide(self) -> None:
        sources = sorted(OBJECT_STORAGE_ROOT.glob("*.py"))
        self.assertTrue(sources, "Object Storage must contain Python sources")
        for path in sources:
            with self.subTest(path=path.name):
                first_line = path.read_text(encoding="utf-8").splitlines()[0]
                self.assertEqual(first_line, MAINTENANCE_HEADER)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

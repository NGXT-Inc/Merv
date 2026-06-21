from __future__ import annotations

import unittest
from datetime import UTC

from backend.utils import parse_iso


class IsoTimestampTest(unittest.TestCase):
    def test_parse_iso_normalizes_expected_shapes(self) -> None:
        self.assertEqual(
            parse_iso("2026-06-21T12:00:00Z").isoformat(),
            "2026-06-21T12:00:00+00:00",
        )
        self.assertEqual(
            parse_iso("2026-06-21T12:00:00+00:00").isoformat(),
            "2026-06-21T12:00:00+00:00",
        )
        naive = parse_iso("2026-06-21T12:00:00")
        self.assertIsNotNone(naive)
        self.assertIs(naive.tzinfo, UTC)

    def test_parse_iso_returns_none_for_absent_or_invalid_values(self) -> None:
        self.assertIsNone(parse_iso(None))
        self.assertIsNone(parse_iso(""))
        self.assertIsNone(parse_iso("not-a-date"))


if __name__ == "__main__":
    unittest.main()

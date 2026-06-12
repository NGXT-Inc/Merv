from __future__ import annotations

import unittest

from backend.config import Mode, resolve_mode
from backend.utils import ValidationError


class ModeConfigTest(unittest.TestCase):
    def test_default_is_local(self) -> None:
        self.assertIs(resolve_mode(env={}), Mode.LOCAL)

    def test_explicit_local(self) -> None:
        self.assertIs(resolve_mode(env={"RESEARCH_PLUGIN_MODE": "local"}), Mode.LOCAL)
        self.assertIs(resolve_mode(env={"RESEARCH_PLUGIN_MODE": " Local "}), Mode.LOCAL)

    def test_planned_modes_fail_with_not_implemented_message(self) -> None:
        for planned in ("control", "daemon"):
            with self.subTest(mode=planned):
                with self.assertRaises(ValidationError) as ctx:
                    resolve_mode(env={"RESEARCH_PLUGIN_MODE": planned})
                self.assertIn("not implemented", ctx.exception.message)

    def test_unknown_mode_fails(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            resolve_mode(env={"RESEARCH_PLUGIN_MODE": "cloud"})
        self.assertIn("unknown", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()

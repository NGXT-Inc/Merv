from __future__ import annotations

import sys
import unittest

from backend.execution.vm_ssh import run_ssh, run_ssh_input


class VmSshSubprocessDecodeTest(unittest.TestCase):
    def test_run_ssh_replaces_invalid_utf8_output(self) -> None:
        result = run_ssh(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'\\x95ok')",
            ]
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "\ufffdok")

    def test_run_ssh_input_replaces_invalid_utf8_output(self) -> None:
        result = run_ssh_input(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "sys.stdout.buffer.write(b'\\x95' + sys.stdin.read().encode())"
                ),
            ],
            "ok",
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "\ufffdok")


if __name__ == "__main__":
    unittest.main()

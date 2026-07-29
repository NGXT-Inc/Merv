"""Functional tests for the merv_run launch wrapper.

These run the real MERV_RUN_SCRIPT with sh against a temp directory standing in
for $MERV_EXPERIMENT_DIR. The contract under test:

- a run detaches (merv_run returns while the command still executes) and the
  WRAPPER writes finished_at then exit_code when the command exits
- meta.json records label/command/pid/started_at
- duplicate labels are refused, bad usage is refused
- the on-box listing command + brain-side parser round-trip the receipts
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from merv.brain.sandbox.execution.run_receipts import (
    MERV_RUN_SCRIPT,
    parse_runs_listing,
    runs_listing_command,
)


class MervRunHarness:
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.experiment_dir = Path(self.tmp.name) / "exp"
        self.experiment_dir.mkdir()
        self.script = Path(self.tmp.name) / "merv_run"
        self.script.write_text(MERV_RUN_SCRIPT)
        self.script.chmod(self.script.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def merv_run(self, *args: str, experiment_dir: str | None = None):
        env = dict(os.environ)
        if experiment_dir is None:
            env["MERV_EXPERIMENT_DIR"] = str(self.experiment_dir)
        else:
            env.pop("MERV_EXPERIMENT_DIR", None)
            if experiment_dir:
                env["MERV_EXPERIMENT_DIR"] = experiment_dir
        return subprocess.run(
            [str(self.script), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def run_dir(self, label: str) -> Path:
        return self.experiment_dir / ".runs" / label

    def wait_for_sentinel(self, label: str, timeout: float = 10.0) -> Path:
        sentinel = self.run_dir(label) / "exit_code"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if sentinel.exists():
                return sentinel
            time.sleep(0.05)
        self.fail(f"exit_code sentinel never appeared for {label}")

    def test_success_run_writes_receipts(self) -> None:
        result = self.merv_run("prep", "--", "sh", "-c", "echo hello out")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("started 'prep'", result.stdout)
        self.wait_for_sentinel("prep")
        run = self.run_dir("prep")
        self.assertEqual((run / "exit_code").read_text().strip(), "0")
        self.assertIn("hello out", (run / "log.txt").read_text())
        self.assertTrue((run / "finished_at").read_text().strip().endswith("Z"))
        meta = json.loads((run / "meta.json").read_text())
        self.assertEqual(meta["label"], "prep")
        self.assertIn("echo hello out", meta["command"])
        self.assertIsInstance(meta["pid"], int)
        self.assertTrue(meta["started_at"].endswith("Z"))

    def test_newline_in_argument_keeps_meta_json_valid(self) -> None:
        # A raw newline inside an argument must not break the one-line receipt.
        result = self.merv_run("nl", "--", "sh", "-c", "echo one\necho two")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_sentinel("nl")
        meta = json.loads((self.run_dir("nl") / "meta.json").read_text())
        self.assertEqual(meta["label"], "nl")
        self.assertIn("echo one", meta["command"])

    def test_failure_exit_code_is_recorded_by_the_wrapper(self) -> None:
        result = self.merv_run("boom", "--", "sh", "-c", "echo pre; exit 3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_sentinel("boom")
        self.assertEqual((self.run_dir("boom") / "exit_code").read_text().strip(), "3")

    def test_run_is_detached_and_outlives_the_launcher(self) -> None:
        # merv_run must return promptly while the command is still executing —
        # the SSH channel (here: the subprocess) is gone before the sentinel.
        started = time.monotonic()
        result = self.merv_run("slow", "--", "sh", "-c", "sleep 1; echo done")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, 1.0, "merv_run did not detach")
        self.assertFalse((self.run_dir("slow") / "exit_code").exists())
        self.wait_for_sentinel("slow")
        self.assertEqual((self.run_dir("slow") / "exit_code").read_text().strip(), "0")

    def test_duplicate_label_is_refused(self) -> None:
        first = self.merv_run("dup", "--", "sh", "-c", "true")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.merv_run("dup", "--", "sh", "-c", "true")
        self.assertEqual(second.returncode, 2)
        self.assertIn("already exists", second.stderr)

    def test_bad_usage_and_bad_label_are_refused(self) -> None:
        self.assertEqual(self.merv_run("nolabel").returncode, 2)
        self.assertEqual(self.merv_run("x", "echo", "hi").returncode, 2)  # no --
        bad = self.merv_run("bad/label", "--", "true")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("label", bad.stderr)

    def test_missing_experiment_dir_is_refused(self) -> None:
        result = self.merv_run("x", "--", "true", experiment_dir="")
        self.assertNotEqual(result.returncode, 0)

    def test_listing_command_round_trips_through_the_parser(self) -> None:
        self.merv_run("done1", "--", "sh", "-c", "exit 7")
        self.merv_run("live1", "--", "sh", "-c", "sleep 30")
        self.wait_for_sentinel("done1")
        listing = subprocess.run(
            ["sh", "-c", runs_listing_command(experiment_dir=str(self.experiment_dir))],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(listing.returncode, 0, listing.stderr)
        runs = {run["label"]: run for run in parse_runs_listing(listing.stdout)}
        self.assertEqual(set(runs), {"done1", "live1"})
        self.assertEqual(runs["done1"]["exit_code"], 7)
        self.assertTrue(runs["done1"]["finished_at"])
        self.assertIsNone(runs["live1"]["exit_code"])
        self.assertEqual(runs["live1"]["finished_at"], "")
        self.assertIn("sleep 30", runs["live1"]["command"])
        self.assertTrue(runs["live1"]["started_at"])

    def test_listing_command_is_a_noop_without_runs_dir(self) -> None:
        listing = subprocess.run(
            ["sh", "-c", runs_listing_command(experiment_dir=str(self.experiment_dir))],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(listing.returncode, 0)
        self.assertEqual(parse_runs_listing(listing.stdout), [])


if __name__ == "__main__":
    unittest.main()


class MetaJsonBoundTest(MervRunHarness, unittest.TestCase):
    """The launcher must bound the one field it records unbounded.

    ARG_MAX allows a command line into the megabytes, and the observer reads
    meta.json under a byte cap. A receipt truncated mid-JSON parses as nothing,
    so the record loses command, pid AND started_at — a clipped command is
    strictly better than no metadata at all.
    """

    def test_a_huge_command_still_yields_a_valid_parseable_receipt(self) -> None:
        huge = ["--flag%d=%s" % (i, "v" * 40) for i in range(6000)]
        raw_len = sum(len(a) + 1 for a in huge)
        self.assertGreater(raw_len, 65536)  # would overflow the reader's cap
        self.merv_run("big", "--", "sh", "-c", "exit 0", *huge)
        self.wait_for_sentinel("big")

        meta_path = self.experiment_dir / ".runs" / "big" / "meta.json"
        meta = json.loads(meta_path.read_text())  # valid JSON, not truncated
        self.assertEqual(meta["label"], "big")
        self.assertTrue(meta["started_at"])
        self.assertLess(meta_path.stat().st_size, 65536)

        listing = subprocess.run(
            ["sh", "-c", runs_listing_command(experiment_dir=str(self.experiment_dir))],
            capture_output=True, text=True, timeout=10,
        )
        parsed = {r["label"]: r for r in parse_runs_listing(listing.stdout)}
        self.assertIn("big", parsed)
        self.assertTrue(parsed["big"]["command"])
        self.assertTrue(parsed["big"]["started_at"])
        self.assertIsNotNone(parsed["big"]["pid"])

    def test_control_bytes_in_a_command_do_not_destroy_the_receipt(self) -> None:
        """JSON forbids raw control bytes, so ANY of them break the receipt.

        esc() normalized only \\n \\r \\t, so a stray \\x01 in an argument made
        meta.json unparseable and silently wiped command, pid AND started_at
        from the ledger row — the run looked like it had no metadata at all.
        """
        self.merv_run("ctrl", "--", "sh", "-c", "exit 0", "a\x01b\x07c café")
        self.wait_for_sentinel("ctrl")

        meta_path = self.experiment_dir / ".runs" / "ctrl" / "meta.json"
        meta = json.loads(meta_path.read_text())  # must not raise
        self.assertEqual(meta["label"], "ctrl")

        listing = subprocess.run(
            ["sh", "-c", runs_listing_command(experiment_dir=str(self.experiment_dir))],
            capture_output=True, text=True, timeout=10,
        )
        parsed = {r["label"]: r for r in parse_runs_listing(listing.stdout)}
        self.assertIn("ctrl", parsed)
        self.assertIsNotNone(parsed["ctrl"]["pid"])
        self.assertTrue(parsed["ctrl"]["started_at"])
        self.assertNotIn("\x01", parsed["ctrl"]["command"])
        self.assertIn("café", parsed["ctrl"]["command"])  # multibyte survives

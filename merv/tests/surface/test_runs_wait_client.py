"""`merv-runs-wait`: the watcher an agent backgrounds to be woken by a run.

The wake signal is two things and both are tested here: the process's EXIT
code, and the one line it leaves on stdout. Four outcomes, two ways in — a
signed URL that needs no credential, and authenticated polling of sandbox.runs
— and the same grammar out of either.

One test drives a REAL brain over a real socket, from the wait_url a real
sandbox.runs row rendered to the answer the mounted route streams back, so the
two sides of this protocol cannot drift apart in silence.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from merv.brain.kernel.secret_tokens import WAIT_SECRET_ENV_VAR
from merv.brain.sandbox.execution.backends.fake import FakeSandboxBackend
from merv.brain.surface.transport.http_server import make_http_server
from merv.client import runs_wait
from merv.client.runs_wait import (
    DEFAULT_DEADLINE_SECONDS,
    EXIT_CODES,
    POLL_FLOOR_SECONDS,
    REGISTRATION_GRACE_SECONDS,
    PollError,
    call_sandbox_runs,
    echo,
    final_line,
    main,
    tool_view,
    watch_keyed,
    watch_url,
)
from tests.support.brain import TestBrain


SECRET = b"wait-secret-for-tests-0123456789abcdef"
UID = "sbx-1"
# Nothing listens on port 1: a wait that answers `transport` immediately.
DEAD_URL = f"http://127.0.0.1:1/wait/{UID}/seed0/deadbeef"
# The shim a platform actually arms, next to the module it runs.
SHIM = Path(runs_wait.__file__).resolve().parents[3] / "bin" / "merv-runs-wait"
# Its two seams a test injects at: the last of the teardown traps, and the
# first statement of everything those traps now come ahead of.
ARMED_ANCHOR = "trap 'forward HUP' HUP"
SETUP_ANCHOR = 'SOURCE="${BASH_SOURCE[0]}"'
# The watchdog's grace, as the shim spells it, so a test can shorten it.
GRACE_ANCHOR = "GRACE=10"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _listing(*runs: dict) -> str:
    """Raw on-box listing text, exactly as runs_listing_command emits it."""
    blocks = []
    for run in runs:
        meta = json.dumps(
            {
                "label": run["label"],
                "command": "python train.py",
                "pid": 4242,
                "started_at": "2026-07-27T10:00:00Z",
            }
        )
        exit_code = run.get("exit_code")
        blocks.append(
            f"===MERV_RUN {_b64(run['label'])}\n"
            f"===META {_b64(meta)}\n"
            f"===EXIT {_b64('' if exit_code is None else str(exit_code))}\n"
            f"===FIN {_b64(run.get('finished_at', ''))}\n"
        )
    return "".join(blocks)


def _row(label: str, status: str, **extra) -> dict:
    return {"label": label, "status": status, **extra}


class _Clock:
    """A clock the test owns: only sleeping (or the poller) moves it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _Poller:
    """A scripted sandbox.runs that records what each call asked for.

    ``holds`` makes the fake server actually spend the wait_seconds it was
    given, which is the only way the long poll's effect on cadence is visible.
    """

    def __init__(self, clock: _Clock, views, *, holds: bool = False) -> None:
        self.clock = clock
        self.views = list(views)
        self.holds = holds
        self.asked: list[int] = []
        self.left: list[float] = []
        self.at: list[float] = []

    def __call__(self, *, sandbox_uid: str, wait_seconds: int, remaining: float) -> dict:
        self.asked.append(wait_seconds)
        self.left.append(remaining)
        self.at.append(self.clock.now)
        view = self.views[min(len(self.asked) - 1, len(self.views) - 1)]
        if self.holds:
            self.clock.now += wait_seconds
        if isinstance(view, Exception):
            raise view
        return view

    @property
    def gaps(self) -> list[float]:
        return [b - a for a, b in zip(self.at, self.at[1:])]


class _Quiet(BaseHTTPRequestHandler):
    """Shared plumbing: no access log, and every request is remembered."""

    protocol_version = "HTTP/1.0"  # close at the end: the client sees real EOF

    def _record(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.seen.append((self.path, self.headers.get("Authorization")))

    def log_message(self, *args) -> None:
        pass


class _StubHandler(_Quiet):
    """Replays a scripted status + line sequence, flushing as it goes."""

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler hook
        self._record()
        status, lines = self.server.script
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        for name, value in self.server.headers_out.items():
            self.send_header(name, value)
        self.end_headers()
        for line in lines:
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

    do_POST = do_GET  # noqa: N815 — the tool wire is a POST


class _HoldHandler(_Quiet):
    """Heartbeats once, then holds — a real wait, stopped where a test wants."""

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler hook
        self._record()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"# waiting 20s\n")
        self.wfile.flush()
        _, tail = self.server.script
        self.server.release.wait(30)
        with suppress(OSError):
            for line in tail:
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()


class _DribbleHandler(_Quiet):
    """An SSE answer that only ever keepalives.

    This is the shape that resets a per-socket-operation timeout forever
    without ever stating an outcome, so a read bounded only by the socket
    never returns.
    """

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler hook
        self._record()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        while not self.server.release.is_set():
            with suppress(OSError):
                self.wfile.write(b": tool call in progress\n\n")
                self.wfile.flush()
            self.server.release.wait(0.05)


class _StallHandler(_Quiet):
    """Dribbles once, then goes silent WITHOUT closing the connection.

    The shape a clock checked BETWEEN reads cannot bound: the check passes on
    the last dribble, and the read that follows it blocks inside the socket
    for its whole timeout — well past the stop the check was enforcing.
    """

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler hook
        self._record()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        with suppress(OSError):
            self.wfile.write(b": tool call in progress\n\n")
            self.wfile.flush()
        self.server.release.wait(30)  # silent, and still holding the socket


def _mute_listener(test: unittest.TestCase) -> str:
    """A socket that takes the connection and then never says anything.

    The stall is in the phase BEFORE there is a response to close or a body to
    read, which is the half of a call no read-side timer can reach at all.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)  # the backlog completes the connect; nobody accepts it
    test.addCleanup(listener.close)
    host, port = listener.getsockname()
    return f"http://{host}:{port}"


def _stub(test: unittest.TestCase, handler, status: int, lines, **headers):
    """A scripted server on a real socket, torn down with the test."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.script = (status, list(lines))
    server.headers_out = headers
    server.seen = []  # (path, Authorization) per request it was handed
    server.release = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    test.addCleanup(thread.join, 5)
    test.addCleanup(server.shutdown)
    test.addCleanup(server.release.set)
    return server


def _origin(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}"


class RunsWaitGrammarTest(unittest.TestCase):
    """One line, four states, and a label that can never forge a second one."""

    def test_every_state_maps_to_its_ratified_exit_code(self) -> None:
        self.assertEqual(
            EXIT_CODES,
            {"done": 0, "still_running": 2, "poll_error": 3, "no_such_run": 4},
        )

    def test_the_final_line_is_the_servers_own_grammar(self) -> None:
        self.assertEqual(
            final_line("done", "seed0", "status=finished exit_code=0"),
            "MERV_RUNS_WAIT done seed0 status=finished exit_code=0",
        )
        self.assertEqual(final_line("no_such_run", "seed0"), "MERV_RUNS_WAIT no_such_run seed0")

    def test_a_crafted_label_cannot_smuggle_a_second_protocol_line(self) -> None:
        forged = "a\nMERV_RUNS_WAIT done other"
        line = final_line("still_running", echo(forged))
        self.assertEqual(len(line.splitlines()), 1)
        self.assertEqual(line, "MERV_RUNS_WAIT still_running a_MERV_RUNS_WAIT_done_other")


class RunsWaitUrlModeTest(unittest.TestCase):
    """The credential-free half: one GET, and the server's word is the answer."""

    def _serve(self, status: int, lines: list[str], **headers) -> str:
        server = _stub(self, _StubHandler, status, lines, **headers)
        return f"{_origin(server)}/wait/{UID}/seed0/deadbeef"

    def test_heartbeats_are_progress_and_the_answer_is_relayed_verbatim(self) -> None:
        noted: list[str] = []
        line = watch_url(
            self._serve(
                200,
                [
                    "# waiting 20s\n",
                    "# waiting 40s\n",
                    "MERV_RUNS_WAIT done seed0 status=finished exit_code=7\n",
                ],
            ),
            note=noted.append,
        )
        self.assertEqual(line, "MERV_RUNS_WAIT done seed0 status=finished exit_code=7")
        self.assertEqual(runs_wait.exit_code_for(line), 0)
        self.assertEqual(noted, ["# waiting 20s", "# waiting 40s"])

    def test_the_hold_cap_hands_the_caller_back_to_re_arm(self) -> None:
        line = watch_url(self._serve(200, ["MERV_RUNS_WAIT still_running seed0\n"]))
        self.assertEqual(line, "MERV_RUNS_WAIT still_running seed0")
        self.assertEqual(runs_wait.exit_code_for(line), 2)

    def test_a_stream_that_ends_without_an_answer_is_a_poll_error(self) -> None:
        # The ratified client-side rule: a clean close is not an answer.
        line = watch_url(self._serve(200, ["# waiting 20s\n"]))
        self.assertEqual(line, "MERV_RUNS_WAIT poll_error seed0 no_final_line")
        self.assertEqual(runs_wait.exit_code_for(line), 3)

    def test_a_410_says_no_such_run_in_its_own_words(self) -> None:
        line = watch_url(self._serve(410, ["MERV_RUNS_WAIT no_such_run seed0\n"]))
        self.assertEqual(line, "MERV_RUNS_WAIT no_such_run seed0")
        self.assertEqual(runs_wait.exit_code_for(line), 4)

    def test_a_429_says_rate_limited_in_its_own_words(self) -> None:
        line = watch_url(self._serve(429, ["MERV_RUNS_WAIT poll_error seed0 rate_limited\n"]))
        self.assertEqual(line, "MERV_RUNS_WAIT poll_error seed0 rate_limited")
        self.assertEqual(runs_wait.exit_code_for(line), 3)

    def test_a_bodyless_refusal_falls_back_to_its_status_code(self) -> None:
        # A proxy that ate the body must not turn a refusal into a mystery.
        for status, expected in ((410, "MERV_RUNS_WAIT no_such_run seed0"),
                                 (429, "MERV_RUNS_WAIT poll_error seed0 rate_limited"),
                                 (500, "MERV_RUNS_WAIT poll_error seed0 no_final_line")):
            with self.subTest(status=status):
                self.assertEqual(watch_url(self._serve(status, [])), expected)

    def test_a_state_this_client_cannot_act_on_is_not_relayed(self) -> None:
        line = watch_url(self._serve(200, ["MERV_RUNS_WAIT teapot seed0\n"]))
        self.assertEqual(line, "MERV_RUNS_WAIT poll_error seed0 malformed")

    def test_a_line_that_is_not_a_whole_answer_is_not_an_answer(self) -> None:
        # The state token is the cheapest part of the grammar to get right, so
        # trusting it alone relays whatever a compromised or broken hop wrote:
        # a `done` with no facts, or one that ends a run this waiter never named.
        for served, why in (
            ("MERV_RUNS_WAIT done\n", "no label at all"),
            ("MERV_RUNS_WAIT done other status=finished exit_code=0\n", "another run"),
            ("MERV_RUNS_WAIT still_running other\n", "another run, still running"),
            ("MERV_RUNS_WAIT done seed0\n", "done without its facts"),
            ("MERV_RUNS_WAIT done seed0 status=finished\n", "half the facts"),
            ("MERV_RUNS_WAIT done seed0 status=finished exit_code=soon\n", "a junk code"),
            ("MERV_RUNS_WAIT done seed0 status=running exit_code=none\n", "a live status"),
            ("MERV_RUNS_WAIT done seed0 status=finished exit_code=0 and_more\n", "a tail"),
        ):
            with self.subTest(why=why):
                line = watch_url(self._serve(200, [served]))
                self.assertEqual(line, "MERV_RUNS_WAIT poll_error seed0 malformed")
                self.assertEqual(runs_wait.exit_code_for(line), 3)

    def test_the_answers_a_real_server_sends_all_still_relay(self) -> None:
        # The validation is a grammar, not a whitelist of one line: every shape
        # the wait route emits has to survive it.
        for served in (
            "MERV_RUNS_WAIT done seed0 status=finished exit_code=0",
            "MERV_RUNS_WAIT done seed0 status=finished exit_code=137",
            "MERV_RUNS_WAIT done seed0 status=lost exit_code=none",
            "MERV_RUNS_WAIT done seed0 status=unknown exit_code=none",
            "MERV_RUNS_WAIT still_running seed0",
            "MERV_RUNS_WAIT poll_error seed0",
            "MERV_RUNS_WAIT poll_error seed0 rate_limited",
            "MERV_RUNS_WAIT no_such_run seed0",
        ):
            with self.subTest(served=served):
                self.assertEqual(watch_url(self._serve(200, [served + "\n"])), served)

    def test_a_redirect_is_refused_rather_than_followed(self) -> None:
        answer = "MERV_RUNS_WAIT done seed0 status=lost exit_code=none\n"
        leak = _stub(self, _StubHandler, 200, [answer])
        url = self._serve(302, [], Location=f"{_origin(leak)}/wait/{UID}/seed0/deadbeef")
        line = watch_url(url)
        self.assertEqual(line, "MERV_RUNS_WAIT poll_error seed0 redirect")
        self.assertEqual(runs_wait.exit_code_for(line), 3)
        self.assertEqual(leak.seen, [])  # and the hop was never taken

    def test_a_dead_endpoint_is_a_poll_error_naming_the_run(self) -> None:
        line = watch_url(f"http://127.0.0.1:1/wait/{UID}/seed0/deadbeef")
        self.assertEqual(line, "MERV_RUNS_WAIT poll_error seed0 transport")

    def test_only_http_urls_are_ever_opened(self) -> None:
        self.assertEqual(
            watch_url("file:///etc/passwd/wait/u/seed0/sig"),
            "MERV_RUNS_WAIT poll_error seed0 bad_url",
        )

    def test_the_label_is_read_out_of_the_url_and_forced_into_its_charset(self) -> None:
        line = watch_url(f"http://127.0.0.1:1/wait/{UID}/see%0Ad0/sig")
        self.assertEqual(line, "MERV_RUNS_WAIT poll_error see_d0 transport")

    def test_stdout_carries_the_answer_and_nothing_else(self) -> None:
        # Platforms wake on output as well as on exit, so a heartbeat on stdout
        # would wake the agent with no answer to read.
        url = self._serve(
            200, ["# waiting 20s\n", "MERV_RUNS_WAIT done seed0 status=lost exit_code=none\n"]
        )
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--url", url])
        self.assertEqual(code, 0)
        self.assertEqual(
            out.getvalue().splitlines(),
            ["MERV_RUNS_WAIT done seed0 status=lost exit_code=none"],
        )
        self.assertIn("# waiting 20s", err.getvalue())


class RunsWaitKeyedModeTest(unittest.TestCase):
    """The authenticated half: sandbox.runs, on a clock the test owns."""

    def setUp(self) -> None:
        self.clock = _Clock()

    def _watch(self, poller: _Poller, *, label: str = "seed0", deadline: float = 600.0) -> str:
        return watch_keyed(
            sandbox_uid=UID,
            label=label,
            deadline=deadline,
            call=poller,
            sleep=self.clock.sleep,
            monotonic=self.clock.monotonic,
        )

    def test_a_finished_run_is_done_with_the_two_facts_a_waiter_gets(self) -> None:
        poller = _Poller(self.clock, [{"runs": [_row("seed0", "finished", exit_code=0)]}])
        self.assertEqual(
            self._watch(poller), "MERV_RUNS_WAIT done seed0 status=finished exit_code=0"
        )

    def test_a_nonzero_exit_is_still_a_completed_observation(self) -> None:
        poller = _Poller(self.clock, [{"runs": [_row("seed0", "finished", exit_code=137)]}])
        line = self._watch(poller)
        self.assertEqual(line, "MERV_RUNS_WAIT done seed0 status=finished exit_code=137")
        self.assertEqual(runs_wait.exit_code_for(line), 0)

    def test_lost_and_unknown_are_terminal_with_no_exit_code(self) -> None:
        # The observation is complete for all three; only `running` waits.
        for status in ("lost", "unknown"):
            with self.subTest(status=status):
                poller = _Poller(self.clock, [{"runs": [_row("seed0", status)]}])
                self.assertEqual(
                    self._watch(poller),
                    f"MERV_RUNS_WAIT done seed0 status={status} exit_code=none",
                )
                self.assertEqual(len(poller.asked), 1)

    def test_a_run_that_ends_mid_wait_resolves_on_that_poll(self) -> None:
        poller = _Poller(
            self.clock,
            [
                {"runs": [_row("seed0", "running")]},
                {"runs": [_row("seed0", "running")]},
                {"runs": [_row("seed0", "finished", exit_code=0)]},
            ],
        )
        self.assertEqual(
            self._watch(poller), "MERV_RUNS_WAIT done seed0 status=finished exit_code=0"
        )
        self.assertEqual(len(poller.asked), 3)

    def test_the_deadline_hands_the_caller_back_to_re_arm(self) -> None:
        poller = _Poller(self.clock, [{"runs": [_row("seed0", "running")]}])
        line = self._watch(poller, deadline=26.0)
        self.assertEqual(line, "MERV_RUNS_WAIT still_running seed0")
        self.assertEqual(runs_wait.exit_code_for(line), 2)
        # It stopped AT the deadline, and never polled to get there faster.
        self.assertEqual(self.clock.now - 1000.0, 26.0)
        self.assertTrue(all(gap >= POLL_FLOOR_SECONDS for gap in poller.gaps), poller.gaps)

    def test_no_two_polls_are_ever_closer_than_the_floor(self) -> None:
        poller = _Poller(self.clock, [{"runs": [_row("seed0", "running")]}])
        self._watch(poller, deadline=61.0)
        self.assertEqual(poller.gaps, [POLL_FLOOR_SECONDS] * (len(poller.at) - 1))

    def test_the_long_poll_is_the_cadence_once_the_run_is_known(self) -> None:
        # A server that actually holds for wait_seconds has already spent the
        # floor, so the client adds no dead time on top of it.
        poller = _Poller(
            self.clock, [{"runs": [_row("seed0", "running")]}], holds=True
        )
        self._watch(poller, deadline=140.0)
        self.assertEqual(poller.asked[0], 0)  # nothing known yet: a short call
        self.assertEqual(poller.asked[1:3], [45, 45])
        self.assertEqual(poller.gaps[1:3], [45.0, 45.0])
        # The last poll never asks the server to hold past the deadline.
        self.assertLessEqual(poller.asked[-1] + (poller.at[-1] - 1000.0), 140.0)

    def test_an_unregistered_label_is_lag_until_the_grace_is_spent(self) -> None:
        poller = _Poller(self.clock, [{"runs": [_row("other", "running")]}])
        line = self._watch(poller, deadline=DEFAULT_DEADLINE_SECONDS)
        self.assertEqual(line, "MERV_RUNS_WAIT no_such_run seed0")
        self.assertEqual(runs_wait.exit_code_for(line), 4)
        # It kept asking for the whole window, in short calls, not one long one.
        self.assertEqual(self.clock.now - 1000.0, REGISTRATION_GRACE_SECONDS)
        self.assertEqual(set(poller.asked), {0})
        self.assertGreater(len(poller.asked), 10)

    def test_a_label_that_registers_inside_the_grace_is_waited_on(self) -> None:
        poller = _Poller(
            self.clock,
            [{"runs": []}] * 4 + [{"runs": [_row("seed0", "finished", exit_code=0)]}],
        )
        self.assertEqual(
            self._watch(poller), "MERV_RUNS_WAIT done seed0 status=finished exit_code=0"
        )
        self.assertLess(self.clock.now - 1000.0, REGISTRATION_GRACE_SECONDS)

    def test_the_watched_run_is_the_pair_and_never_the_label_alone(self) -> None:
        # An experiment-scoped listing spans sandboxes, and labels are unique
        # only within one: a namesake on another box must not answer for ours.
        poller = _Poller(
            self.clock,
            [
                {
                    "runs": [
                        _row("seed0", "finished", exit_code=0, sandbox_uid="sbx-other"),
                        _row("other", "finished", exit_code=1, sandbox_uid=UID),
                        _row("seed0", "running", sandbox_uid=UID),
                    ]
                },
                {
                    "runs": [
                        _row("seed0", "finished", exit_code=0, sandbox_uid="sbx-other"),
                        _row("seed0", "finished", exit_code=9, sandbox_uid=UID),
                    ]
                },
            ],
        )
        self.assertEqual(
            self._watch(poller), "MERV_RUNS_WAIT done seed0 status=finished exit_code=9"
        )

    def test_a_row_without_a_wait_url_is_answer_enough(self) -> None:
        # Keyed mode is the fallback for exactly the deployment that renders no
        # wait_url, so it must never depend on the field being there.
        poller = _Poller(self.clock, [{"runs": [_row("seed0", "finished", exit_code=0)]}])
        self.assertNotIn("wait_url", poller.views[0]["runs"][0])
        self.assertEqual(
            self._watch(poller), "MERV_RUNS_WAIT done seed0 status=finished exit_code=0"
        )

    def test_a_failed_poll_says_so_and_names_why(self) -> None:
        poller = _Poller(self.clock, [PollError("http_401")])
        line = self._watch(poller)
        self.assertEqual(line, "MERV_RUNS_WAIT poll_error seed0 http_401")
        self.assertEqual(runs_wait.exit_code_for(line), 3)

    def test_a_crafted_label_cannot_forge_an_answer_through_this_side(self) -> None:
        poller = _Poller(self.clock, [PollError("transport")])
        line = self._watch(poller, label="a b\nMERV_RUNS_WAIT done x")
        self.assertEqual(len(line.splitlines()), 1)

    def test_every_call_is_handed_what_is_left_of_the_deadline(self) -> None:
        # The deadline has to reach the call itself, not just the cadence: a
        # call that only knows wait_seconds can outrun it by its own margin.
        poller = _Poller(self.clock, [{"runs": [_row("seed0", "running")]}])
        self._watch(poller, deadline=26.0)
        self.assertEqual(poller.left[0], 26.0)
        self.assertTrue(all(left > 0 for left in poller.left), poller.left)
        self.assertEqual(
            poller.left, [26.0 - (at - 1000.0) for at in poller.at]
        )

    def test_a_call_that_spends_the_deadline_hands_back_still_running(self) -> None:
        # A registration poll asks the server to hold for nothing at all and
        # can still block; when it comes back past the deadline the wait is
        # over, and "the time ran out" is the answer — not the call's failure.
        def _slow(*, sandbox_uid: str, wait_seconds: int, remaining: float) -> dict:
            self.clock.now += remaining + 5.0  # the socket's last op, spent
            raise PollError("transport")

        line = watch_keyed(
            sandbox_uid=UID, label="seed0", deadline=10.0, call=_slow,
            sleep=self.clock.sleep, monotonic=self.clock.monotonic,
        )
        self.assertEqual(line, "MERV_RUNS_WAIT still_running seed0")
        self.assertEqual(runs_wait.exit_code_for(line), 2)

    def test_an_answer_that_arrived_late_is_still_an_answer(self) -> None:
        # Being past the deadline never discards an outcome already observed:
        # exit 0 is for a run this watcher SAW end, whenever the line landed.
        def _late(*, sandbox_uid: str, wait_seconds: int, remaining: float) -> dict:
            self.clock.now += remaining
            return {"runs": [_row("seed0", "finished", exit_code=0)]}

        line = watch_keyed(
            sandbox_uid=UID, label="seed0", deadline=10.0, call=_late,
            sleep=self.clock.sleep, monotonic=self.clock.monotonic,
        )
        self.assertEqual(line, "MERV_RUNS_WAIT done seed0 status=finished exit_code=0")

    def test_a_poll_that_fails_inside_the_deadline_still_names_why(self) -> None:
        # The still_running override is for the deadline, not a mask over it.
        poller = _Poller(self.clock, [PollError("http_500")])
        self.assertEqual(
            self._watch(poller, deadline=600.0), "MERV_RUNS_WAIT poll_error seed0 http_500"
        )


class RunsWaitTransportTest(unittest.TestCase):
    """The tool-call envelope: both response shapes, and every refusal."""

    def _envelope(self, view: dict) -> dict:
        return {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": json.dumps(view)}],
            "structuredContent": view,
        }}

    def test_a_plain_json_answer_yields_the_tools_view(self) -> None:
        view = {"runs": [_row("seed0", "running")]}
        self.assertEqual(tool_view(json.dumps(self._envelope(view))), view)

    def test_an_sse_framed_answer_is_read_past_its_keepalives(self) -> None:
        view = {"runs": [_row("seed0", "finished", exit_code=0)]}
        body = (
            ": tool call in progress\n\n"
            ": tool call in progress\n\n"
            f"event: message\ndata: {json.dumps(self._envelope(view))}\n\n"
        )
        self.assertEqual(tool_view(body), view)

    def test_a_view_that_arrived_only_as_text_is_still_read(self) -> None:
        view = {"runs": []}
        envelope = self._envelope(view)
        del envelope["result"]["structuredContent"]
        self.assertEqual(tool_view(json.dumps(envelope)), view)

    def test_a_refused_call_is_a_poll_error_not_an_empty_listing(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "no"}})
        with self.assertRaises(PollError) as caught:
            tool_view(body)
        self.assertEqual(caught.exception.reason, "tool_error")

    def test_nonsense_on_the_wire_is_a_poll_error(self) -> None:
        for body in ("", "not json", "[]", '{"jsonrpc":"2.0","id":1}', "event: message\n\n"):
            with self.subTest(body=body), self.assertRaises(PollError):
                tool_view(body)

    def test_a_rejected_key_names_its_status_so_the_caller_can_tell(self) -> None:
        server = _stub(self, _StubHandler, 401, ['{"detail":"unauthorized"}'])
        with self.assertRaises(PollError) as caught:
            call_sandbox_runs(
                control_url=_origin(server), key="mk_x", project_id="p",
                sandbox_uid=UID, wait_seconds=0,
            )
        self.assertEqual(caught.exception.reason, "http_401")

    def test_a_brain_that_does_not_answer_is_a_poll_error(self) -> None:
        with self.assertRaises(PollError) as caught:
            call_sandbox_runs(
                control_url="http://127.0.0.1:1", key="mk_x", project_id="p",
                sandbox_uid=UID, wait_seconds=0,
            )
        self.assertEqual(caught.exception.reason, "transport")

    def test_a_redirect_never_carries_the_key_to_wherever_it_points(self) -> None:
        # urllib follows 3xx by default and takes the Authorization header
        # along, so one misconfigured hop would disclose MERV_MCP_KEY to
        # whatever answered. The control URL is canonical: refuse instead.
        leak = _stub(self, _StubHandler, 200, ['{"jsonrpc":"2.0","id":1,"result":{}}'])
        server = _stub(self, _StubHandler, 302, [], Location=f"{_origin(leak)}/mcp")
        with self.assertRaises(PollError) as caught:
            call_sandbox_runs(
                control_url=_origin(server), key="mk_secret", project_id="p",
                sandbox_uid=UID, wait_seconds=0,
            )
        # Measured before the fix: the hop's target logged `Bearer mk_secret`.
        self.assertEqual(leak.seen, [])
        self.assertEqual([auth for _, auth in server.seen], ["Bearer mk_secret"])
        self.assertEqual(caught.exception.reason, "redirect")
        refused = final_line("poll_error", "seed0", "redirect")
        self.assertEqual(runs_wait.exit_code_for(refused), 3)

    def test_the_socket_never_gets_a_timeout_longer_than_the_budget(self) -> None:
        seen: list[float] = []

        class _Refuses:
            def open(self, request, timeout=None):
                seen.append(timeout)
                raise urllib.error.URLError("nope")

        # wait_seconds + margin is the ceiling, what is left of the deadline is
        # the floor, and a registration call inherits the deadline, not the 30s.
        with patch.object(runs_wait, "_OPENER", _Refuses()):
            for wait_seconds, remaining, expected in (
                (45, 600.0, 75.0), (45, 10.0, 15.0), (0, 10.0, 15.0), (0, None, 30.0)
            ):
                with self.subTest(remaining=remaining), self.assertRaises(PollError):
                    call_sandbox_runs(
                        control_url="http://brain.test", key="mk_x", project_id="p",
                        sandbox_uid=UID, wait_seconds=wait_seconds, remaining=remaining,
                    )
                self.assertEqual(seen[-1], expected)

    def test_a_stream_that_only_keepalives_cannot_outlive_the_deadline(self) -> None:
        # urllib's timeout is per socket OPERATION, so keepalive bytes reset it
        # forever: without a wall clock over the read, this watcher never wakes.
        server = _stub(self, _DribbleHandler, 200, [])
        deadline = 2.0
        started = time.monotonic()
        line = watch_keyed(
            sandbox_uid=UID,
            label="seed0",
            deadline=deadline,
            call=lambda **kwargs: call_sandbox_runs(
                control_url=_origin(server), key="mk_x", project_id="p", **kwargs
            ),
        )
        spent = time.monotonic() - started
        self.assertEqual(line, "MERV_RUNS_WAIT still_running seed0")
        self.assertEqual(runs_wait.exit_code_for(line), 2)
        self.assertGreaterEqual(spent, deadline)
        self.assertLess(spent, deadline + runs_wait.KEYED_OVERRUN_SECONDS)


class RunsWaitWallClockTest(unittest.TestCase):
    """The bound the caller was promised, against servers that go quiet.

    urllib's timeout bounds one socket OPERATION, and a call is several of
    them; each shape below spends its stall inside one, where a clock read
    between operations never gets a turn. What must hold for all of them is
    one sentence: the call is back by the deadline it was handed, plus grace.
    """

    DEADLINE = 1.0
    # Room for a thread hand-off and a socket teardown, and far less than the
    # per-op timeout the old read could still spend past the stop: measured
    # before the fix, every case below took ~6s for this deadline.
    GRACE = 2.0

    def _assert_bounded(self, control_url: str) -> None:
        started = time.monotonic()
        line = watch_keyed(
            sandbox_uid=UID,
            label="seed0",
            deadline=self.DEADLINE,
            call=lambda **kwargs: call_sandbox_runs(
                control_url=control_url, key="mk_x", project_id="p", **kwargs
            ),
        )
        spent = time.monotonic() - started
        # A wait that ran out of time learned nothing, and says so.
        self.assertEqual(line, "MERV_RUNS_WAIT still_running seed0")
        self.assertEqual(runs_wait.exit_code_for(line), 2)
        self.assertGreaterEqual(spent, self.DEADLINE)
        self.assertLess(spent, self.DEADLINE + self.GRACE)
        # ...and the contract as advertised, which the grace sits well inside.
        self.assertLess(spent, self.DEADLINE + runs_wait.KEYED_OVERRUN_SECONDS)

    def test_a_body_that_stalls_after_dribbling_cannot_outlive_the_deadline(self) -> None:
        self._assert_bounded(_origin(_stub(self, _StallHandler, 200, [])))

    def test_a_server_that_never_answers_at_all_cannot_either(self) -> None:
        self._assert_bounded(_mute_listener(self))


class RunsWaitEndToEndTest(unittest.TestCase):
    """A real brain on a real socket: the row it renders, the wait it answers.

    Both halves of the protocol meet here — sandbox.runs hands back a wait_url,
    and that exact URL is what this client consumes — so a change to either
    side that the other did not follow fails at this seam.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        repo = Path(self.tmp.name)
        self.fake = FakeSandboxBackend()
        self.brain = TestBrain(
            repo_root=repo,
            db_path=repo / ".research_plugin" / "state.sqlite",
            execution_backend=self.fake,
        )
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.brain.shutdown)
        self.project_id = self.brain.call_tool(
            "project", {"action": "create", "name": "Waits"}
        )["id"]
        experiment_id = self.brain.call_tool(
            "experiment.create",
            {"project_id": self.project_id, "name": "long-training", "intent": "x"},
        )["id"]
        with self.brain.store.transaction() as conn:
            conn.execute(
                "UPDATE experiments SET status = 'ready_to_run' WHERE id = ?",
                (experiment_id,),
            )
        self.sandbox_uid = self.brain.call_tool(
            "sandbox.request",
            {"project_id": self.project_id, "experiment_id": experiment_id},
        )["sandbox_uid"]
        row = self.brain.sandboxes.repository.get_by_uid(sandbox_uid=self.sandbox_uid)
        self.fake.run_listings[str(row["sandbox_id"])] = _listing(
            {"label": "seed0", "exit_code": 0, "finished_at": "2026-07-27T10:05:00Z"},
            {"label": "seed1"},
        )
        self.brain.sandboxes.runs_observer.observe_live(max_age_seconds=0.0)
        self.base = self._serve()

    def _serve(self) -> str:
        server = make_http_server(
            self.brain, port=0, env={WAIT_SECRET_ENV_VAR: SECRET.decode("ascii")}
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 10)
        self.addCleanup(server.shutdown)
        host, port = server.server_address
        return f"http://{host}:{port}"

    def _view(self) -> dict:
        return call_sandbox_runs(
            control_url=self.base,
            key="mk_local",
            project_id=self.project_id,
            sandbox_uid=self.sandbox_uid,
            wait_seconds=0,
        )

    def test_the_url_a_real_row_renders_is_the_one_this_client_can_wait_on(self) -> None:
        run = runs_wait._row_for(self._view(), sandbox_uid=self.sandbox_uid, label="seed0")
        self.assertIsNotNone(run)
        line = watch_url(run["wait_url"])
        self.assertEqual(line, "MERV_RUNS_WAIT done seed0 status=finished exit_code=0")
        self.assertEqual(runs_wait.exit_code_for(line), 0)

    def test_a_tag_the_brain_never_signed_is_a_real_410(self) -> None:
        line = watch_url(f"{self.base}/wait/{self.sandbox_uid}/seed0/{'0' * 32}")
        self.assertEqual(line, "MERV_RUNS_WAIT no_such_run seed0")
        self.assertEqual(runs_wait.exit_code_for(line), 4)

    def test_keyed_mode_reads_the_same_brain_through_the_tool_wire(self) -> None:
        line = watch_keyed(
            sandbox_uid=self.sandbox_uid,
            label="seed0",
            deadline=30.0,
            call=lambda **kwargs: call_sandbox_runs(
                control_url=self.base, key="mk_local",
                project_id=self.project_id, **kwargs
            ),
        )
        self.assertEqual(line, "MERV_RUNS_WAIT done seed0 status=finished exit_code=0")


class RunsWaitCliTest(unittest.TestCase):
    """A bad invocation still has to answer in the protocol, never in argparse."""

    def _run(self, argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
        out = io.StringIO()
        with (
            patch.dict("os.environ", env or {}, clear=False),
            redirect_stdout(out),
            redirect_stderr(io.StringIO()),
        ):
            code = main(argv)
        return code, out.getvalue().strip()

    def test_a_usage_error_is_a_poll_error_and_never_exit_two(self) -> None:
        # Changed with the totality fix: once parsing has captured a label,
        # every line names it. A caller with several watchers armed should not
        # have to guess which one just failed; before parsing there is nothing
        # to name, so those lines still say `_`.
        for argv, label in (
            ([], "_"),
            (["--label", "seed0"], "seed0"),
            (["--url", "http://x/wait/u/from_url/s", "--label", "l"], "from_url"),
        ):
            with self.subTest(argv=argv):
                code, line = self._run(argv)
                self.assertEqual(code, 3)
                self.assertEqual(line, f"MERV_RUNS_WAIT poll_error {label} usage")

    def test_an_unparseable_deadline_leaves_through_the_protocol(self) -> None:
        code, line = self._run(
            ["--project-id", "p", "--sandbox-uid", UID, "--label", "seed0",
             "--deadline", "soon"]
        )
        self.assertEqual((code, line), (3, "MERV_RUNS_WAIT poll_error _ usage"))

    def test_keyed_mode_without_a_key_is_a_poll_error(self) -> None:
        # The label is named here too: this usage error is found after parsing.
        code, line = self._run(
            ["--project-id", "p", "--sandbox-uid", UID, "--label", "seed0"],
            env={"MERV_MCP_KEY": "", "RESEARCH_PLUGIN_MCP_KEY": ""},
        )
        self.assertEqual((code, line), (3, "MERV_RUNS_WAIT poll_error seed0 usage"))

    def test_nothing_leaves_this_process_outside_the_grammar(self) -> None:
        # The caller is a background process watching for one prefix and four
        # exit codes; a traceback out of here would strand it forever. It also
        # names the run, like an interrupt does: a crash is a failure to
        # observe THIS run, and the caller re-arms on that run alone.
        def _boom(**kwargs):
            raise RuntimeError("the wire caught fire")

        with patch.object(runs_wait, "call_sandbox_runs", _boom):
            code, line = self._run(
                ["--project-id", "p", "--sandbox-uid", UID, "--label", "seed0"],
                env={"MERV_MCP_KEY": "mk_secret"},
            )
        self.assertEqual((code, line), (3, "MERV_RUNS_WAIT poll_error seed0 crashed"))

    def test_a_crash_after_parsing_still_names_the_run_it_was_watching(self) -> None:
        # The crash need not come from the wire: anything raised after the
        # label is known answers for that label.
        with patch.object(runs_wait, "_watch", lambda args: 1 / 0):
            code, line = self._run(["--url", "http://x/wait/u/seed0/sig"])
        self.assertEqual((code, line), (3, "MERV_RUNS_WAIT poll_error seed0 crashed"))

    def test_the_key_and_the_control_url_come_from_the_client_environment(self) -> None:
        seen: dict = {}

        def _call(*, control_url: str, key: str, **kwargs):
            seen.update(control_url=control_url, key=key, **kwargs)
            return {"runs": [_row("seed0", "finished", exit_code=0)]}

        with patch.object(runs_wait, "call_sandbox_runs", _call):
            code, line = self._run(
                ["--project-id", "p1", "--sandbox-uid", UID, "--label", "seed0"],
                env={"MERV_MCP_KEY": "mk_secret",
                     "MERV_CONTROL_URL": "https://brain.example.test/"},
            )
        self.assertEqual((code, line), (0, "MERV_RUNS_WAIT done seed0 status=finished exit_code=0"))
        self.assertEqual(seen["control_url"], "https://brain.example.test")
        self.assertEqual(seen["key"], "mk_secret")
        self.assertEqual(seen["project_id"], "p1")


class _RealProcess(unittest.TestCase):
    """Plumbing for the tests that spawn the artifact instead of importing it.

    In-process tests can pin what ``main`` returns; only a real one can pin
    what the platform actually observes — the exit code the OS reports, and
    what survives on stdout when the parent has already walked away.
    """

    def _argv(self, *args: str) -> list[str]:
        return [sys.executable, "-m", "merv.client.runs_wait", *args]

    def _env(self) -> dict[str, str]:
        return {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}

    def _held(self, *lines: str) -> tuple[str, ThreadingHTTPServer]:
        """A wait URL that really holds, and the server that ends the holding."""
        server = _stub(self, _HoldHandler, 200, lines)
        return f"{_origin(server)}/wait/{UID}/seed0/deadbeef", server

    def _spawn(
        self,
        url: str,
        argv: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.Popen:
        proc = subprocess.Popen(
            argv or self._argv("--url", url), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env or self._env(),
        )
        self.addCleanup(proc.kill)
        # The heartbeat is the handshake: it says the watcher is really waiting
        # and has written nothing to stdout yet.
        self.assertTrue(proc.stderr.readline().startswith("# waiting"))
        return proc

    def _stray_shim(self) -> tuple[Path, str]:
        """The shim in a tree with no src/ for `merv` to come from."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stray = Path(tmp.name) / "bin"
        stray.mkdir()
        copy = stray / SHIM.name
        shutil.copy2(SHIM, copy)
        return copy, tmp.name

    def _stub_python(self, body: str) -> str:
        """A stand-in for the interpreter the shim resolves.

        The shim's belts are for a child that never became a watcher, so a
        test has to pin what that child does — which a real python running the
        real module, by construction, cannot be made to do.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stub = Path(tmp.name) / "python-stub"
        stub.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
        stub.chmod(0o755)
        return str(stub)

    def _shim_env(
        self, *, python: str = "", path: str = "", tools: str = "", tmpdir: str = ""
    ) -> dict[str, str]:
        search = os.environ.get("PATH", "")
        env = {
            # `tools` goes in FRONT: denying the shim a program it shells out
            # to is the only way to stand in for a machine this one is not.
            "PATH": f"{tools}{os.pathsep}{search}" if tools else search,
            "MERV_PYTHON": python or sys.executable,
            "PYTHONPATH": path or os.pathsep.join(p for p in sys.path if p),
        }
        if tmpdir:
            env["TMPDIR"] = tmpdir
        return env

    def _shim(
        self, shim: Path, *args: str, path: str = "", python: str = "",
        tools: str = "", tmpdir: str = "",
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(shim), *args], capture_output=True, text=True, timeout=60,
            env=self._shim_env(python=python, path=path, tools=tools, tmpdir=tmpdir),
        )

    def _refusing_mktemp(self) -> str:
        """A PATH entry whose `mktemp` never succeeds: a machine with nowhere
        writable at all, which this one — with its real /tmp — is not."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake = Path(tmp.name) / "mktemp"
        fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake.chmod(0o755)
        return tmp.name

    def _sentinel_python(self, marker: Path, line: str) -> str:
        """A stand-in that records having run at all, then says the most
        dangerous thing it could: a `done` with a clean exit code."""
        return self._stub_python(f': > "{marker}"\nprintf "{line}\\n"\nexit 0\n')

    def _armed_python(self, marker: Path) -> str:
        """A stand-in child that arms for a teardown the instant it starts.

        The real watcher needs a quarter of a second to reach its own handler,
        which would make what a forwarded signal DID an artifact of the
        interpreter rather than of the shim. A shell arms in a millisecond, and
        says plainly which of the two things happened to it. It also records
        having run at all, which is how a test tells a teardown the shim
        forwarded from one it never had to.
        """
        return self._stub_python(
            f': > "{marker}"\n'
            'trap \'printf "MERV_RUNS_WAIT poll_error seed0 terminated\\n"; exit 3\' TERM\n'
            "N=0\n"
            "while [ $N -lt 40 ]; do sleep 0.05; N=$((N+1)); done\n"
            'printf "MERV_RUNS_WAIT still_running seed0\\n"\n'
            "exit 2\n"
        )

    def _deaf_python(self) -> str:
        """A stand-in child that ignores the teardown outright and waits on.

        The shape of a forward that never landed — the send that died with the
        child's own exec looks exactly like this from the shim's side — and the
        one a re-send could never have fixed either.
        """
        return self._stub_python(
            "trap '' TERM\n"
            'printf "# waiting on nothing\\n" >&2\n'
            "while :; do sleep 0.2; done\n"
        )

    def _hasty_shim(self, seconds: float) -> Path:
        """The shim with the watchdog's grace cut down to a test's patience.

        Ten seconds is for a real watcher stating a real line on a loaded
        machine. What a test needs to see is only that the grace ENDS: a child
        that never answers is taken out of the way and answered for, rather
        than waited on by a caller nobody is going to wake.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = Path(tmp.name) / SHIM.name
        text = SHIM.read_text(encoding="utf-8")
        self.assertIn(GRACE_ANCHOR, text)
        copy.write_text(text.replace(GRACE_ANCHOR, f"GRACE={seconds}", 1), encoding="utf-8")
        copy.chmod(0o755)
        return copy

    def _stalled_shim(self, seconds: float) -> Path:
        """The shim, announcing its traps and then stalling before the child.

        Two probes, injected at seams of the artifact rather than around it.
        The handshake fires the instant the traps are armed, so a test signals
        a script that is really listening instead of racing bash's own startup
        — a signal delivered before line 1 is the untotalizable residual, and
        no placement of a handler wins that one. The stall then holds the shim
        in the phase where the traps exist and the child does not, which is the
        window the signal has to survive.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = Path(tmp.name) / SHIM.name
        text = SHIM.read_text(encoding="utf-8")
        for anchor in (ARMED_ANCHOR, SETUP_ANCHOR):
            self.assertIn(anchor, text)
        text = text.replace(ARMED_ANCHOR, f"{ARMED_ANCHOR}\nprintf '# armed\\n' >&2", 1)
        text = text.replace(SETUP_ANCHOR, f"sleep {seconds}\n{SETUP_ANCHOR}", 1)
        copy.write_text(text, encoding="utf-8")
        copy.chmod(0o755)
        return copy


class RunsWaitProcessTest(_RealProcess):
    """The artifact itself: a real process, and every way out of it."""

    def test_help_is_an_answer_on_the_wake_channel_like_any_other(self) -> None:
        # `-h` used to print to stdout and exit 0 — indistinguishable from an
        # observed terminal run by exit code, with no grammar line to read.
        result = subprocess.run(
            self._argv("--help"), capture_output=True, text=True,
            env=self._env(), timeout=60,
        )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout.splitlines(), ["MERV_RUNS_WAIT poll_error _ usage"])
        self.assertIn("usage: merv-runs-wait", result.stderr)

    def test_an_interrupt_leaves_through_the_grammar_and_names_the_run(self) -> None:
        url, _ = self._held()
        proc = self._spawn(url)
        proc.send_signal(signal.SIGINT)
        out, _ = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(out.splitlines(), ["MERV_RUNS_WAIT poll_error seed0 interrupted"])

    def test_a_termination_leaves_through_the_grammar_like_an_interrupt(self) -> None:
        # SIGTERM is how a platform reclaims a background process, and its
        # default action is a silent 143 with an empty stdout — the caller's
        # own teardown, arriving as a crash nobody can read.
        url, _ = self._held()
        proc = self._spawn(url)
        proc.terminate()
        out, _ = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(out.splitlines(), ["MERV_RUNS_WAIT poll_error seed0 terminated"])

    def test_a_parent_that_hung_up_still_gets_the_code_it_earned(self) -> None:
        # The exit code is the half of the wake signal a dead pipe cannot eat:
        # a BrokenPipeError on the final write must not replace it.
        url, server = self._held("MERV_RUNS_WAIT done seed0 status=finished exit_code=0\n")
        proc = self._spawn(url)
        proc.stdout.close()  # the parent shell went away mid-wait
        server.release.set()  # and only now does the answer arrive
        self.assertEqual(proc.wait(timeout=60), 0)
        err = proc.stderr.read()
        proc.stderr.close()
        self.assertNotIn("BrokenPipeError", err)
        self.assertNotIn("Traceback", err)

    def test_an_importer_that_exits_on_main_is_as_total_as_the_module(self) -> None:
        # `main` is importable, and the protection against the interpreter's
        # own final flush used to live in the `__main__` block alone: the same
        # dead pipe then died at shutdown instead, as exit 120 — not a code in
        # the contract, and not one the caller can act on.
        url, server = self._held("MERV_RUNS_WAIT done seed0 status=finished exit_code=0\n")
        importer = (
            "import sys\n"
            "from merv.client.runs_wait import main\n"
            "raise SystemExit(main(['--url', sys.argv[1]]))\n"
        )
        proc = self._spawn(url, [sys.executable, "-c", importer, url])
        proc.stdout.close()
        server.release.set()
        self.assertEqual(proc.wait(timeout=60), 0)
        err = proc.stderr.read()
        proc.stderr.close()
        self.assertNotIn("Traceback", err)

    def test_a_watcher_spawned_with_stdout_closed_still_exits_its_code(self) -> None:
        # fd 1 closed before the interpreter starts leaves `sys.stdout` as
        # None, and the answer with nowhere to go. The exit code is the other
        # half of the wake signal and it is still owed, so the write must fail
        # silently rather than become an AttributeError and exit 1.
        result = subprocess.run(
            ["/bin/sh", "-c", 'exec 1>&-; exec "$0" "$@"', *self._argv("--url", DEAD_URL)],
            capture_output=True, text=True, env=self._env(), timeout=60,
        )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_the_shim_hands_the_watchers_own_answer_straight_through(self) -> None:
        result = self._shim(SHIM, "--url", DEAD_URL)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(
            result.stdout.splitlines(), ["MERV_RUNS_WAIT poll_error seed0 transport"]
        )

    def test_the_shim_answers_for_a_python_that_never_reached_the_module(self) -> None:
        # The outermost belt: an import that failed, or an interpreter killed
        # before it could install a handler, never reaches this module at all,
        # and a caller blocked on the process would wake to a traceback and an
        # exit code that means nothing in the grammar.
        copy, tree = self._stray_shim()
        result = self._shim(copy, "--url", DEAD_URL, path=tree)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout.splitlines(), ["MERV_RUNS_WAIT poll_error _ crashed"])
        self.assertIn("No module named", result.stderr)


class RunsWaitTotalityTest(_RealProcess):
    """The windows in which a total watcher was still not total.

    Each of these was a real escape: a note on the wake channel, a belt that
    failed before it could belt, a signal landing in the one window nothing
    was watching, and an exit code no line stood behind.
    """

    def test_a_closed_stderr_never_pushes_a_note_onto_the_wake_channel(self) -> None:
        # `print(file=None)` FALLS BACK TO STDOUT, and fd 2 closed at startup
        # leaves `sys.stderr` as None for the life of the process — so the
        # shared legacy-env deprecation note printed itself above the grammar
        # line, on the one channel that carries the wake signal.
        result = subprocess.run(
            ["/bin/sh", "-c", 'exec 2>&-; exec "$0" "$@"',
             *self._argv("--project-id", "p", "--sandbox-uid", UID,
                         "--label", "seed0", "--deadline", "5")],
            capture_output=True, text=True, timeout=60,
            env={
                **self._env(),
                "MERV_MCP_KEY": "",
                "RESEARCH_PLUGIN_MCP_KEY": "mk_legacy",  # the legacy spelling warns
                "MERV_CONTROL_URL": "http://127.0.0.1:1",  # nothing listens: one poll
            },
        )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(
            result.stdout.splitlines(), ["MERV_RUNS_WAIT poll_error seed0 transport"]
        )

    def test_the_shim_maps_its_exit_with_its_own_stdout_closed(self) -> None:
        # The shim's belt is a write and an exit, and under `set -e` the write
        # failing took the exit with it: fd 1 closed plus a child that never
        # reached the module left exit 1, which is not in the grammar at all.
        copy, tree = self._stray_shim()
        result = subprocess.run(
            ["/bin/sh", "-c", 'exec 1>&-; exec "$0" "$@"', str(copy), "--url", DEAD_URL],
            capture_output=True, text=True, timeout=60,
            env=self._shim_env(path=tree),
        )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_a_signal_in_the_final_window_cannot_unsay_the_answer(self) -> None:
        # Between the write and the return there is nothing left to do but
        # exit, and a SIGTERM landing there used to raise straight out of
        # `main`: a valid line, then a traceback, then exit 1 under it.
        code, out, err = self._run_python(
            "import os, signal, sys\n"
            "from merv.client import runs_wait\n"
            "said = runs_wait._say\n"
            "def say(line):\n"
            "    written = said(line)\n"
            "    os.kill(os.getpid(), signal.SIGTERM)  # reclaimed mid-answer\n"
            "    return written\n"
            "runs_wait._say = say\n"
            "raise SystemExit(runs_wait.main(['--url', sys.argv[1]]))\n"
        )
        self.assertEqual(code, 3)
        self.assertEqual(out.splitlines(), ["MERV_RUNS_WAIT poll_error seed0 transport"])
        self.assertNotIn("Traceback", err)

    def test_a_second_teardown_signal_cannot_escape_the_first_ones_answer(self) -> None:
        # A platform that sends SIGTERM twice, or a terminal that sends the
        # signal the shim also forwards: the second landed inside the clause
        # already answering for the first and escaped it — no line at all.
        code, out, err = self._run_python(
            "import os, signal, sys\n"
            "from merv.client import runs_wait\n"
            "def watch(args):\n"
            "    os.kill(os.getpid(), signal.SIGTERM)\n"
            "    raise AssertionError('the first signal never landed')\n"
            "runs_wait._watch = watch\n"
            "noted, again = runs_wait._diagnostic, []\n"
            "def note(text):\n"
            "    noted(text)\n"
            "    if not again:\n"
            "        again.append(1)\n"
            "        os.kill(os.getpid(), signal.SIGTERM)  # while answering the first\n"
            "runs_wait._diagnostic = note\n"
            "raise SystemExit(runs_wait.main(['--url', sys.argv[1]]))\n"
        )
        self.assertEqual(code, 3)
        self.assertEqual(out.splitlines(), ["MERV_RUNS_WAIT poll_error seed0 terminated"])
        self.assertIn("merv-runs-wait: terminated", err)  # the second was really sent
        self.assertNotIn("Traceback", err)

    def _run_python(self, program: str, url: str = DEAD_URL) -> tuple[int, str, str]:
        result = subprocess.run(
            [sys.executable, "-c", program, url],
            capture_output=True, text=True, env=self._env(), timeout=60,
        )
        return result.returncode, result.stdout, result.stderr

    def test_a_host_that_exits_inside_the_answer_cannot_say_it_twice(self) -> None:
        # `main` is importable, and a host can leave through the very flush
        # that delivers the line: SystemExit is not an Exception, so it walked
        # out of the write path into the crash belt, which then said a SECOND
        # thing on the wake channel and returned 3 over an answer already
        # written and already worth 4. A line that has landed is the wake
        # signal — somebody is reading it — so it is also the exit code.
        server = _stub(self, _StubHandler, 410, ["MERV_RUNS_WAIT no_such_run seed0\n"])
        code, out, err = self._run_python(
            "import sys\n"
            "from merv.client import runs_wait\n"
            "real, spent = sys.stdout, []\n"
            "class Host:\n"
            "    def write(self, text):\n"
            "        return real.write(text)\n"
            "    def flush(self):\n"
            "        real.flush()\n"
            "        if not spent:\n"
            "            spent.append(1)\n"
            "            raise SystemExit(9)  # the host's own exit, mid-answer\n"
            "sys.stdout = Host()\n"
            "raise SystemExit(runs_wait.main(['--url', sys.argv[1]]))\n",
            url=f"{_origin(server)}/wait/{UID}/seed0/deadbeef",
        )
        self.assertEqual(out.splitlines(), ["MERV_RUNS_WAIT no_such_run seed0"])
        self.assertEqual(code, 4)
        self.assertNotIn("Traceback", err)

    def test_a_host_that_exits_from_inside_the_write_cannot_say_it_twice(self) -> None:
        # The same escape one instruction earlier, and the one the latch used
        # to miss entirely: the host leaves through `write` rather than the
        # flush after it. The whole line was on the wake channel and the latch
        # was still down — it went up after the write RETURNED, and this write
        # never returns — so the crash belt found nothing claimed and said a
        # second, contradicting thing under an answer already being read.
        # Claiming the answer before offering the first byte is what makes the
        # order of those two impossible to get wrong.
        server = _stub(self, _StubHandler, 410, ["MERV_RUNS_WAIT no_such_run seed0\n"])
        code, out, err = self._run_python(
            "import sys\n"
            "from merv.client import runs_wait\n"
            "real, spent = sys.stdout, []\n"
            "class Host:\n"
            "    def write(self, text):\n"
            "        real.write(text)\n"
            "        real.flush()\n"
            "        if not spent:\n"
            "            spent.append(1)\n"
            "            raise SystemExit(9)  # the host's own exit, mid-write\n"
            "        return len(text)\n"
            "    def flush(self):\n"
            "        real.flush()\n"
            "sys.stdout = Host()\n"
            "raise SystemExit(runs_wait.main(['--url', sys.argv[1]]))\n",
            url=f"{_origin(server)}/wait/{UID}/seed0/deadbeef",
        )
        self.assertEqual(out.splitlines(), ["MERV_RUNS_WAIT no_such_run seed0"])
        self.assertEqual(code, 4)  # the answer's own code, not the belt's 3
        self.assertNotIn("Traceback", err)

    def test_a_stream_that_takes_the_line_in_pieces_still_gets_all_of_it(self) -> None:
        # A short write is not a refusal: `write` returns what it TOOK, and a
        # pipe under pressure or a host stand-in that dribbles takes less than
        # it was handed. The answer was offered once and latched whole
        # regardless, so `MERV_RU` could stand on the wake channel as a
        # complete answer with the rest of the line never offered to anybody.
        server = _stub(self, _StubHandler, 410, ["MERV_RUNS_WAIT no_such_run seed0\n"])
        code, out, err = self._run_python(
            "import sys\n"
            "from merv.client import runs_wait\n"
            "real = sys.stdout\n"
            "class Trickle:\n"
            "    def write(self, text):\n"
            "        real.write(text[:1])\n"
            "        real.flush()\n"
            "        return 1  # one character of it, and it says so\n"
            "    def flush(self):\n"
            "        real.flush()\n"
            "sys.stdout = Trickle()\n"
            "raise SystemExit(runs_wait.main(['--url', sys.argv[1]]))\n",
            url=f"{_origin(server)}/wait/{UID}/seed0/deadbeef",
        )
        self.assertEqual(out.splitlines(), ["MERV_RUNS_WAIT no_such_run seed0"])
        self.assertEqual(code, 4)
        self.assertNotIn("Traceback", err)

    def test_a_stream_that_stops_taking_leaves_part_of_a_line_and_no_second(self) -> None:
        # And the end of that loop: a stream that takes seven characters and
        # then takes nothing, ever. The offering stops rather than spinning on
        # a writer making no progress, and what it managed is NOT finished
        # down the raw-fd path — a partial line is the `poll_error` a consumer
        # re-arms on, where a partial line with a whole one written under it
        # is two answers. The code the observation earned survives both.
        server = _stub(self, _StubHandler, 410, ["MERV_RUNS_WAIT no_such_run seed0\n"])
        code, out, err = self._run_python(
            "import sys\n"
            "from merv.client import runs_wait\n"
            "real, sent = sys.stdout, []\n"
            "class Cliff:\n"
            "    def write(self, text):\n"
            "        if len(sent) >= 7:\n"
            "            return 0  # and not one character more, ever\n"
            "        sent.append(1)\n"
            "        real.write(text[:1])\n"
            "        real.flush()\n"
            "        return 1\n"
            "    def flush(self):\n"
            "        real.flush()\n"
            "sys.stdout = Cliff()\n"
            "raise SystemExit(runs_wait.main(['--url', sys.argv[1]]))\n",
            url=f"{_origin(server)}/wait/{UID}/seed0/deadbeef",
        )
        self.assertEqual(out, "MERV_RU")
        self.assertEqual(code, 4)
        self.assertNotIn("Traceback", err)

    def test_the_shim_refuses_an_exit_code_with_no_line_behind_it(self) -> None:
        # 0/2/3/4 used to pass through unexamined, so a child that exited 0
        # before the watcher ever ran woke a caller keying on the exit code
        # with a `done` and an empty stdout to read it from.
        result = self._shim(SHIM, "--url", DEAD_URL, python=self._stub_python("exit 0\n"))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout.splitlines(), ["MERV_RUNS_WAIT poll_error _ crashed"])

    def test_a_line_already_stated_outlives_the_exit_code_that_lost_it(self) -> None:
        # Killed on the way out, the watcher leaves a whole observation and an
        # exit code that means nothing. The line is the answer and the answer
        # names the code, so the line is what the code follows. The belt used
        # to APPEND a second line here instead — and "last line wins" is no
        # rule at all for a consumer woken by the first one it reads.
        stub = self._stub_python(
            'printf "MERV_RUNS_WAIT done seed0 status=finished exit_code=0\\n"\n'
            "kill -KILL $$\n"
        )
        result = self._shim(SHIM, "--url", DEAD_URL, python=stub)
        self.assertEqual(
            result.stdout.splitlines(),
            ["MERV_RUNS_WAIT done seed0 status=finished exit_code=0"],
        )
        self.assertEqual(result.returncode, 0)

    def test_the_shim_forwards_the_signal_it_was_actually_sent(self) -> None:
        # It sent TERM for all three, so a Ctrl-C came back as `terminated`:
        # the caller was told the platform had reclaimed a watcher it stopped.
        for signum, reason in (
            (signal.SIGINT, "interrupted"),
            (signal.SIGTERM, "terminated"),
        ):
            with self.subTest(reason=reason):
                url, _ = self._held()
                proc = self._spawn(url, [str(SHIM), "--url", url], env=self._shim_env())
                proc.send_signal(signum)
                out, _ = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 3)
                self.assertEqual(
                    out.splitlines(), [f"MERV_RUNS_WAIT poll_error seed0 {reason}"]
                )

    def test_the_shim_relays_a_healthy_answer_untouched(self) -> None:
        # The capture is a belt, not a filter: the line arrives byte for byte,
        # the exit code verbatim, and the notes stay on a live stderr — which
        # is where a platform's own progress watching reads them.
        stub = self._stub_python(
            'printf "# waiting 20s\\n" >&2\n'
            'printf "MERV_RUNS_WAIT no_such_run seed0\\n"\n'
            "exit 4\n"
        )
        result = self._shim(SHIM, "--url", DEAD_URL, python=stub)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stdout, "MERV_RUNS_WAIT no_such_run seed0\n")
        self.assertEqual(result.stderr, "# waiting 20s\n")

    def test_an_unusable_tmpdir_is_not_the_end_of_the_capture(self) -> None:
        # TMPDIR is a caller's preference, not the only writable place there
        # is, and degrading to no capture is the expensive way to lose one.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        line = "MERV_RUNS_WAIT done seed0 status=finished exit_code=0"
        stub = self._stub_python(f'printf "{line}\\n"\nexit 7\n')  # a nonsense code
        result = self._shim(
            SHIM, "--url", DEAD_URL, python=stub,
            tmpdir=str(Path(tmp.name) / "no-such-dir"),
        )
        # Captured, so the LINE is the answer and the 7 never reaches anybody.
        self.assertEqual(result.stdout.splitlines(), [line])
        self.assertEqual(result.returncode, 0)

    def test_with_nowhere_to_capture_the_shim_never_runs_the_child_at_all(self) -> None:
        # Degrading to a passthrough was the whole escape in one move: the exit
        # code went back to being trusted unvalidated, and the child's stdout
        # went back to being able to put a second line under a first one
        # somebody had already woken on. A machine with no writable temp dir
        # cannot be watched from, and saying so is a total, honest re-arm —
        # where a `done` that might be lying is neither.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        marker = Path(tmp.name) / "the-child-ran"
        result = self._shim(
            SHIM, "--url", DEAD_URL,
            python=self._sentinel_python(
                marker, "MERV_RUNS_WAIT done seed0 status=finished exit_code=0"
            ),
            tools=self._refusing_mktemp(),
        )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout.splitlines(), ["MERV_RUNS_WAIT poll_error _ crashed"])
        self.assertFalse(marker.exists())  # and nothing was launched to lie

    def test_the_teardown_traps_are_the_first_thing_the_shim_does(self) -> None:
        # A signal delivered before bash has executed line 1 kills the script
        # outright — rc -15 and an empty stdout, the exact shape everything
        # here exists to prevent — and no handler can be installed early enough
        # to catch that one: it is the same class of thing as SIGKILL. What CAN
        # be closed is the rest of the script's own startup, which used to run
        # a symlink walk, several subshells and an interpreter search with the
        # teardown signals still at their defaults. Order is the fix, so order
        # is what this pins; the behavior is the test below it.
        text = SHIM.read_text(encoding="utf-8")
        for trap in ("trap 'forward INT' INT", "trap 'forward TERM' TERM", ARMED_ANCHOR):
            with self.subTest(trap=trap):
                self.assertLess(text.index(trap), text.index(SETUP_ANCHOR))

    def test_a_teardown_that_beats_the_child_aborts_the_launch(self) -> None:
        # The half of that window a handler can have: a signal that lands once
        # the traps are armed but before there is a pid to forward it to. It
        # was first DROPPED — the caller's own teardown, answered by nobody,
        # with the wait running on as if nothing had been asked of it — and
        # then buffered for delivery, which is the repair this one replaces:
        # sent in the same breath as the fork, the signal reaches a child that
        # is still the shell about to become the watcher and dies with the
        # exec, so a bounded volley could clear the buffer having delivered
        # nothing at all. Nothing needs delivering if nothing is started. The
        # launch is ABORTED and the shim answers under the signal's own name.
        #
        # (This assertion is one of the two the round-6 fixes deliberately
        # changed: the outcome used to be a race between the child's own line
        # and a generic `crashed`, and is now neither and not a race.)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        marker = Path(tmp.name) / "the-child-ran"
        stalled = self._stalled_shim(0.6)
        armed = self._armed_python(marker)
        for attempt in range(3):  # it is a race; run it more than once
            with self.subTest(attempt=attempt):
                proc = subprocess.Popen(
                    [str(stalled), "--url", DEAD_URL], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True,
                    env=self._shim_env(python=armed),
                )
                self.addCleanup(proc.kill)
                # The handshake: armed, and stalled where there is no child.
                self.assertEqual(proc.stderr.readline().strip(), "# armed")
                proc.terminate()
                out, _ = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 3)
                # The teardown reached the run, by the only route that cannot
                # lose it: it named the signal the caller actually sent, and
                # nothing was ever started for that signal to have to catch.
                self.assertEqual(
                    out.splitlines(), ["MERV_RUNS_WAIT poll_error _ terminated"]
                )
                self.assertFalse(marker.exists())

    def test_a_child_deaf_to_the_teardown_is_ended_and_answered_for(self) -> None:
        # The lost forward, in the only shape a test can hold still: a child
        # that ignores the signal outright, which from the shim's side is what
        # a send that died with the child's exec looks like too. Re-sending it
        # could not tell the two apart — nothing acknowledges — so delivery
        # stopped being the thing that has to work. The grace runs out, the
        # child is taken out of the way, `wait` returns on its own, and the
        # shim states the teardown itself rather than waiting on a run nobody
        # managed to stop or calling it a generic crash.
        proc = self._spawn(
            DEAD_URL, [str(self._hasty_shim(0.5)), "--url", DEAD_URL],
            env=self._shim_env(python=self._deaf_python()),
        )
        proc.terminate()
        out, _ = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(out.splitlines(), ["MERV_RUNS_WAIT poll_error _ terminated"])

    def test_a_second_teardown_through_the_shim_cannot_add_a_line(self) -> None:
        # The shim forwards, the child answers, and the platform signals again
        # while both are still finishing: one line and one code, whichever of
        # the two the second signal lands in.
        url, _ = self._held()
        proc = self._spawn(url, [str(SHIM), "--url", url], env=self._shim_env())
        proc.terminate()
        proc.terminate()
        out, _ = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(out.splitlines(), ["MERV_RUNS_WAIT poll_error seed0 terminated"])

    def test_the_shim_lets_go_of_the_reaped_pid_before_anything_else_runs(self) -> None:
        # `kill -0 "$CHILD"` stood between the reap and the clear: a trap
        # firing in those few statements forwarded a real signal to a pid the
        # watcher no longer held, and which the kernel may by then have
        # reissued to somebody else's process. The probe is gone — the loop
        # asks bash rather than the OS whether a signal interrupted it — and
        # the clear is the first statement after the wait, which leaves one
        # statement of exposure: the same untotalizable class as a signal
        # delivered before line 1. Order is the fix, so order is what this
        # pins; the behavior around it is the rest of this class.
        lines = [ln.strip() for ln in SHIM.read_text(encoding="utf-8").splitlines()]
        waits = [i for i, ln in enumerate(lines) if ln.startswith("wait ")]
        self.assertEqual(len(waits), 1)
        self.assertEqual(lines[waits[0] + 1], 'CHILD=""')
        self.assertNotIn("kill -0", "\n".join(lines))

    def test_the_shims_done_grammar_is_exactly_the_watchers_own(self) -> None:
        # Presence is not the fact: `status=running` is a live run and
        # `exit_code=garbage` is no code at all, and a recognizer that looked
        # only for the two words mapped this to exit 0 — the one answer that
        # tells a caller its waiting is over.
        forged = "MERV_RUNS_WAIT done seed0 status=running exit_code=garbage"
        self.assertIsNone(runs_wait._FACTS_RE.match("status=running exit_code=garbage"))
        result = self._shim(
            SHIM, "--url", DEAD_URL,
            python=self._stub_python(f'printf "{forged}\\n"\nexit 0\n'),
        )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout.splitlines(), ["MERV_RUNS_WAIT poll_error _ crashed"])

    def test_every_done_the_watcher_can_state_survives_the_shim(self) -> None:
        # The other side of that seam: the shell recognizer is a grammar, not a
        # whitelist, and tightening it must not turn a real observation into a
        # re-arm. Every facts string _FACTS_RE admits comes through untouched.
        for facts in (
            "status=finished exit_code=0",
            "status=finished exit_code=137",
            "status=finished exit_code=-9",
            "status=lost exit_code=none",
            "status=unknown exit_code=none",
        ):
            with self.subTest(facts=facts):
                self.assertIsNotNone(runs_wait._FACTS_RE.match(facts))
                line = f"MERV_RUNS_WAIT done seed0 {facts}"
                result = self._shim(
                    SHIM, "--url", DEAD_URL,
                    python=self._stub_python(f'printf "{line}\\n"\nexit 0\n'),
                )
                self.assertEqual((result.returncode, result.stdout), (0, f"{line}\n"))


if __name__ == "__main__":
    unittest.main()

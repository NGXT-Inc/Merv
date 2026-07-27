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
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        self.at: list[float] = []

    def __call__(self, *, sandbox_uid: str, wait_seconds: int) -> dict:
        self.asked.append(wait_seconds)
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


class _StubHandler(BaseHTTPRequestHandler):
    """Replays a scripted status + line sequence, flushing as it goes."""

    protocol_version = "HTTP/1.0"  # close at the end: the client sees real EOF

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler hook
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        status, lines = self.server.script
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        for line in lines:
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

    do_POST = do_GET  # noqa: N815 — the tool wire is a POST

    def log_message(self, *args) -> None:
        pass


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

    def _serve(self, status: int, lines: list[str]) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        server.script = (status, lines)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        host, port = server.server_address
        return f"http://{host}:{port}/wait/{UID}/seed0/deadbeef"

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
        server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        server.script = (401, ['{"detail":"unauthorized"}'])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        host, port = server.server_address
        with self.assertRaises(PollError) as caught:
            call_sandbox_runs(
                control_url=f"http://{host}:{port}", key="mk_x", project_id="p",
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
        for argv in ([], ["--label", "seed0"], ["--url", "http://x/wait/u/l/s", "--label", "l"]):
            with self.subTest(argv=argv):
                code, line = self._run(argv)
                self.assertEqual(code, 3)
                self.assertEqual(line, "MERV_RUNS_WAIT poll_error _ usage")

    def test_an_unparseable_deadline_leaves_through_the_protocol(self) -> None:
        code, line = self._run(
            ["--project-id", "p", "--sandbox-uid", UID, "--label", "seed0",
             "--deadline", "soon"]
        )
        self.assertEqual((code, line), (3, "MERV_RUNS_WAIT poll_error _ usage"))

    def test_keyed_mode_without_a_key_is_a_poll_error(self) -> None:
        code, line = self._run(
            ["--project-id", "p", "--sandbox-uid", UID, "--label", "seed0"],
            env={"MERV_MCP_KEY": "", "RESEARCH_PLUGIN_MCP_KEY": ""},
        )
        self.assertEqual((code, line), (3, "MERV_RUNS_WAIT poll_error _ usage"))

    def test_nothing_leaves_this_process_outside_the_grammar(self) -> None:
        # The caller is a background process watching for one prefix and four
        # exit codes; a traceback out of here would strand it forever.
        def _boom(**kwargs):
            raise RuntimeError("the wire caught fire")

        with patch.object(runs_wait, "call_sandbox_runs", _boom):
            code, line = self._run(
                ["--project-id", "p", "--sandbox-uid", UID, "--label", "seed0"],
                env={"MERV_MCP_KEY": "mk_secret"},
            )
        self.assertEqual((code, line), (3, "MERV_RUNS_WAIT poll_error _ crashed"))

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


if __name__ == "__main__":
    unittest.main()

"""runs-wait: the one watcher an agent arms to be woken when a run ends.

Every agent platform can background a process and notice when it exits, and
almost none of them can be woken any other way. So that is what this ships: a
process that blocks while a detached ``merv_run`` runs, and whose EXIT — plus
the single line it leaves on stdout — is the wake signal.

Two ways in, one grammar out. With a signed ``wait_url`` from a sandbox.runs
row it is one streaming GET and no credential at all; without one it is
authenticated polling of sandbox.runs over the same wire ``merv-client env``
prints. Either way the last line of stdout is
``MERV_RUNS_WAIT <state> <label> [status=... exit_code=...]`` and the exit code
is the state, so a platform can watch whichever of the two it can see.

Exit 0 means the run reached a TERMINAL state — the observation completed, not
that the work succeeded; the caller branches on ``status=``/``exit_code=``.
Exit 2 (timed out) and 3 (the wait itself failed) both mean re-arm. Exit 4 is
the only conclusive absence.

Stdlib only, like the rest of ``merv.client``: this runs from a bare python3
on a machine that installed nothing.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from merv.shared.client_config import dual_env_value, resolve_client_control_url


MCP_KEY_ENV_VAR = "MERV_MCP_KEY"

FINAL_PREFIX = "MERV_RUNS_WAIT "
DONE = "done"
STILL_RUNNING = "still_running"
POLL_ERROR = "poll_error"
NO_SUCH_RUN = "no_such_run"
# The wake contract. 0 says the observation finished, never that the workload
# succeeded; 2 and 3 both mean "re-arm"; 4 is the one conclusive absence.
EXIT_CODES = {DONE: 0, STILL_RUNNING: 2, POLL_ERROR: 3, NO_SUCH_RUN: 4}

# Never two authenticated polls closer together than this, whatever the server
# did with wait_seconds.
POLL_FLOOR_SECONDS = 5.0
# sandbox.runs' own long poll, at the documented ceiling: a finished run wakes
# this process seconds later instead of at the next tick.
LONG_POLL_SECONDS = 45
# merv_run writes its receipt on the box before the brain has mirrored it, so a
# label the mirror has never heard of is registration lag until this has passed.
REGISTRATION_GRACE_SECONDS = 90.0
# The server's hold cap, so both modes hand the caller back at the same rhythm.
DEFAULT_DEADLINE_SECONDS = 3600.0
# Per READ, not per stream: a hold lasts up to the cap and heartbeats every
# ~20s, so this only fires on a connection that actually died.
STREAM_READ_TIMEOUT_SECONDS = 120.0
# Headroom over the long poll the server is holding for us.
KEYED_CALL_MARGIN_SECONDS = 30.0

# `finished`, `lost` and `unknown` are all ends of the observation; only
# `running` is a reason to keep waiting.
TERMINAL_RUN_STATUSES = frozenset({"finished", "lost", "unknown"})

_MAX_ECHO_CHARS = 128
# The server forces its echoed label into merv_run's charset before it goes on
# the wire; this side must too, or a crafted label could forge a second
# protocol line and wake a platform with an answer nobody sent.
_UNSAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9._-]")


class UsageError(Exception):
    """A bad invocation. Never argparse's SystemExit(2): 2 is still_running."""


class PollError(Exception):
    """A poll that could not answer: transport, auth, or a refused call."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def echo(label: str) -> str:
    """The label as it may go on the wire, in merv_run's own charset."""
    return _UNSAFE_LABEL_RE.sub("_", label)[:_MAX_ECHO_CHARS] or "_"


def final_line(state: str, label: str, extra: str = "") -> str:
    tail = f" {extra}" if extra else ""
    return f"{FINAL_PREFIX}{state} {label}{tail}"


def exit_code_for(line: str) -> int:
    return EXIT_CODES.get(_state_of(line), EXIT_CODES[POLL_ERROR])


def _state_of(line: str) -> str:
    if not line.startswith(FINAL_PREFIX):
        return ""
    return line[len(FINAL_PREFIX):].split(" ", 1)[0]


def _note(line: str) -> None:
    """Progress goes to stderr; stdout is reserved for the answer."""
    if line:
        print(line, file=sys.stderr, flush=True)


# ---------- url mode: one streaming GET, no credential ----------


def watch_url(
    url: str,
    *,
    read_timeout: float = STREAM_READ_TIMEOUT_SECONDS,
    note: Callable[[str], None] = _note,
) -> str:
    """Hold the signed wait URL open until the server states an outcome.

    The server's own grammar line is the answer and is relayed verbatim; a
    stream that ends without one told us nothing, which is poll_error even
    when the socket closed cleanly.
    """
    label = _label_from_wait_url(url)
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        return final_line(POLL_ERROR, label, "bad_url")
    try:
        response: Any = urllib.request.urlopen(url, timeout=read_timeout)
        status = int(getattr(response, "status", 0) or 0)
    except urllib.error.HTTPError as exc:
        # A refusal is a response: 410 and 429 carry their own protocol line.
        response, status = exc, int(exc.code)
    except (urllib.error.URLError, OSError):
        return final_line(POLL_ERROR, label, "transport")
    with contextlib.closing(response):
        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith(FINAL_PREFIX):
                    note(line)  # heartbeats are progress, not answers
                    continue
                return _relayed(line, label=label)
        except (urllib.error.URLError, OSError):
            return final_line(POLL_ERROR, label, "transport")
    if status == 410:
        return final_line(NO_SUCH_RUN, label)
    if status == 429:
        return final_line(POLL_ERROR, label, "rate_limited")
    return final_line(POLL_ERROR, label, "no_final_line")


def _relayed(line: str, *, label: str) -> str:
    """The server's line verbatim — unless it states nothing we can act on."""
    state = _state_of(line)
    return line if state in EXIT_CODES else final_line(POLL_ERROR, label, "malformed")


def _label_from_wait_url(url: str) -> str:
    """The label out of /wait/{sandbox_uid}/{label}/{sig}, echo-safe."""
    parts = urllib.parse.urlsplit(url).path.strip("/").split("/")
    if len(parts) < 3:
        return "_"
    return echo(urllib.parse.unquote(parts[-2]))


# ---------- keyed mode: authenticated polling of sandbox.runs ----------


def watch_keyed(
    *,
    sandbox_uid: str,
    label: str,
    deadline: float,
    call: Callable[..., dict[str, Any]],
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> str:
    """Poll sandbox.runs for one (sandbox_uid, label) until it settles.

    Two clocks bound the loop and neither may bend the other: no two calls are
    ever closer together than the floor, and the deadline is never crossed by
    issuing one more call — whichever comes first simply ends the wait.
    """
    echoed = echo(label)
    started = monotonic()
    next_poll = started
    seen = False
    while True:
        delay = min(next_poll, started + deadline) - monotonic()
        if delay > 0:
            sleep(delay)
        elapsed = monotonic() - started
        if elapsed >= deadline:
            return final_line(STILL_RUNNING, echoed)
        if not seen and elapsed >= REGISTRATION_GRACE_SECONDS:
            return final_line(NO_SUCH_RUN, echoed)
        # An unregistered label answers immediately however long the poll asks
        # for, so the grace window is short calls in a loop; only a run the
        # mirror already knows earns sandbox.runs' own long poll.
        budget = int(min(LONG_POLL_SECONDS, deadline - elapsed)) if seen else 0
        next_poll = monotonic() + POLL_FLOOR_SECONDS
        try:
            view = call(sandbox_uid=sandbox_uid, wait_seconds=budget)
        except PollError as exc:
            return final_line(POLL_ERROR, echoed, exc.reason)
        run = _row_for(view, sandbox_uid=sandbox_uid, label=label)
        if run is not None:
            seen = True
            status = str(run.get("status") or "")
            if status in TERMINAL_RUN_STATUSES:
                return final_line(DONE, echoed, _facts(run, status=status))


def _row_for(
    view: dict[str, Any], *, sandbox_uid: str, label: str
) -> dict[str, Any] | None:
    """The one run this watcher named. Labels are unique per sandbox only, so
    an experiment-scoped listing can carry a namesake from another box."""
    for run in view.get("runs") or []:
        if not isinstance(run, dict) or str(run.get("label") or "") != label:
            continue
        uid = str(run.get("sandbox_uid") or "")
        if uid and uid != sandbox_uid:
            continue
        return run
    return None


def _facts(run: dict[str, Any], *, status: str) -> str:
    """The two facts a waiter gets, spelled exactly as the server spells them."""
    code = run.get("exit_code")
    rendered = "none"
    if code is not None:
        with contextlib.suppress(TypeError, ValueError):
            rendered = str(int(code))
    return f"status={status} exit_code={rendered}"


def call_sandbox_runs(
    *, control_url: str, key: str, project_id: str, sandbox_uid: str, wait_seconds: int
) -> dict[str, Any]:
    """One sandbox.runs call on the same HTTP MCP wire merv-client prints."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "sandbox.runs",
            "arguments": {
                "project_id": project_id,
                "sandbox_uid": sandbox_uid,
                "wait_seconds": wait_seconds,
            },
        },
    }
    request = urllib.request.Request(
        f"{control_url}/mcp",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Streamable HTTP may answer either way, and a long poll usually
            # streams — its keepalive comments are what cross a proxy.
            "Accept": "application/json, text/event-stream",
        },
    )
    timeout = wait_seconds + KEYED_CALL_MARGIN_SECONDS
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise PollError(f"http_{int(exc.code)}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise PollError("transport") from exc
    return tool_view(body)


def tool_view(body: str) -> dict[str, Any]:
    """The tool's structured result out of a JSON or SSE-framed response."""
    message = _last_json_message(body)
    if message.get("error") is not None:
        raise PollError("tool_error")
    result = message.get("result")
    if not isinstance(result, dict):
        raise PollError("malformed")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for block in result.get("content") or []:
        text = block.get("text") if isinstance(block, dict) else None
        if not text:
            continue
        with contextlib.suppress(ValueError, TypeError):
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
    raise PollError("malformed")


def _last_json_message(body: str) -> dict[str, Any]:
    raw = body.strip()
    if raw and not raw.startswith("{"):
        # SSE framing: the answer is the last `data:` line, after however many
        # keepalive comments and progress notifications preceded it.
        raw = next(
            (
                line[len("data:"):].strip()
                for line in reversed(raw.splitlines())
                if line.startswith("data:")
            ),
            "",
        )
    try:
        message = json.loads(raw)
    except ValueError as exc:
        raise PollError("malformed") from exc
    if not isinstance(message, dict):
        raise PollError("malformed")
    return message


# ---------- entry point ----------


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(None if argv is None else list(argv))
        line = _watch(args)
    except UsageError as exc:
        print(f"merv-runs-wait: {exc}", file=sys.stderr)
        line = final_line(POLL_ERROR, "_", "usage")
    except Exception as exc:  # noqa: BLE001 — a watcher that dies wakes nobody
        # Whatever went wrong, the caller is a background process watching for
        # this grammar: leaving through a traceback would strand it.
        print(f"merv-runs-wait: {exc!r}", file=sys.stderr)
        line = final_line(POLL_ERROR, "_", "crashed")
    # Stdout carries the final line and nothing else, so a platform watching
    # output wakes exactly once and on the answer.
    print(line, flush=True)
    return exit_code_for(line)


def _watch(args: argparse.Namespace) -> str:
    keyed = (args.project_id, args.sandbox_uid, args.label)
    if args.url:
        if any(keyed):
            raise UsageError("--url takes no --project-id/--sandbox-uid/--label")
        return watch_url(args.url)
    if not all(keyed):
        raise UsageError(
            "keyed mode needs --project-id, --sandbox-uid and --label "
            "(or pass the --url from a sandbox.runs row)"
        )
    if args.deadline <= 0:
        raise UsageError("--deadline must be positive")
    key = dual_env_value(MCP_KEY_ENV_VAR)
    if not key:
        raise UsageError(f"{MCP_KEY_ENV_VAR} is required to poll sandbox.runs")
    control_url = resolve_client_control_url()
    return watch_keyed(
        sandbox_uid=args.sandbox_uid,
        label=args.label,
        deadline=float(args.deadline),
        call=lambda **kwargs: call_sandbox_runs(
            control_url=control_url, key=key, project_id=args.project_id, **kwargs
        ),
    )


class _Parser(argparse.ArgumentParser):
    """Usage errors leave through the protocol, not through argparse's exit 2."""

    def error(self, message: str):  # noqa: D102 — argparse hook
        raise UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="merv-runs-wait",
        description=(
            "Block until a detached merv_run ends, then exit: 0 terminal "
            "(read status=/exit_code= on the final line), 2 still running "
            "(re-arm), 3 poll error, 4 no such run."
        ),
    )
    parser.add_argument(
        "--url",
        help="Signed wait_url from a sandbox.runs row. Needs no key.",
    )
    parser.add_argument(
        "--project-id", help="Keyed mode: project the sandbox belongs to."
    )
    parser.add_argument(
        "--sandbox-uid", help="Keyed mode: sandbox the run was launched on."
    )
    parser.add_argument(
        "--label",
        help="Keyed mode: merv_run label — unique within its sandbox only.",
    )
    parser.add_argument(
        "--deadline",
        type=float,
        default=DEFAULT_DEADLINE_SECONDS,
        help=(
            "Keyed mode: report still_running after this many seconds "
            f"(default {int(DEFAULT_DEADLINE_SECONDS)})."
        ),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())

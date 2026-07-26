"""Pure helpers, constants, and value types for the sandbox stack.

Everything here is free of ``SandboxService`` state — module-level functions,
tunables, and pure projection helpers.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..kernel.ports.sandbox_lifecycle import DEFAULT_STALE_PROVISION_DEADLINE_SECONDS
from ..kernel.utils import ValidationError, parse_iso as _parse_iso


VALID_GPUS: frozenset[str] = frozenset(
    {"T4", "L4", "A10G", "L40S", "A100", "A100-80GB", "H100", "B200"}
)
ACTIVE_SANDBOX_STATUSES: frozenset[str] = frozenset({"running"})
# A destroy attempt the provider never confirmed. Deliberately NOT terminal: no
# sweep revisits a terminal row, so a live VM behind one bills forever (audit
# SAN-05). The row stays visible and the retry sweep keeps asking.
CLEANUP_PENDING_STATUS = "cleanup_pending"
# Reached the end of the line: the VM is gone and every attachment is closed.
TERMINAL_SANDBOX_STATUSES: frozenset[str] = frozenset({"terminated", "failed"})
# Statuses a fresh sandbox.request must not reuse the row of.
UNREUSABLE_SANDBOX_STATUSES: frozenset[str] = TERMINAL_SANDBOX_STATUSES | {
    CLEANUP_PENDING_STATUS
}
MAX_TIME_LIMIT_SECONDS = 24 * 60 * 60
MIN_TIME_LIMIT_SECONDS = 60
DEFAULT_TIME_LIMIT_SECONDS = 3600
DEFAULT_CPU = 2.0
DEFAULT_MEMORY_MB = 8192

# How long sandbox.request waits for a fresh provision to finish before it
# returns `provisioning` and tells the agent to poll. Kept safely under the MCP
# client timeout (~60s) so the call never trips it.
DEFAULT_REQUEST_WAIT_SECONDS = 45.0
# sandbox.runs long-poll: the server re-lists .runs receipts every POLL seconds
# and returns early on any terminal transition. The CAP is a server ceiling for
# clients with generous tool timeouts; clients at the common ~60s MCP floor
# (see DEFAULT_REQUEST_WAIT_SECONDS above) should pass wait_seconds<=45. The
# HTTP MCP caller must allow the requested long-poll window.
RUNS_WAIT_CAP_SECONDS = 300.0
RUNS_WAIT_POLL_SECONDS = 5.0
# Backstop: a `provisioning` row this old whose job is no longer in this process
# (daemon restart, or a wedged acquire) is reconciled to `failed`.
DEFAULT_STALE_PROVISION_SECONDS = 15 * 60.0
# Cadence hint handed to the agent while provisioning. Lambda VMs commonly
# take 5-15 minutes to boot and bootstrap, so a tighter cadence just burns
# calls without learning anything new.
POLL_AFTER_SECONDS = 30
# Live-usage samples are coalesced for this long so the fleet view and the
# drill-in terminal (which both poll ~3s) don't double-exec into a sandbox.
METRICS_CACHE_TTL_SECONDS = 2.0
# How often the reaper checks for sandboxes past their expires_at deadline and
# terminates them. Needed because Lambda VMs (unlike Modal sandboxes) have no
# server-side lifetime enforcement, so without this an expired VM bills forever.
DEFAULT_REAPER_INTERVAL_SECONDS = 30.0
DEFAULT_SANDBOX_IDLE_SECONDS = 3600.0
# How long a `cleanup_pending` row waits before the sweep asks the provider
# again, indexed by attempts already made. The last entry repeats forever: a
# possibly-billing VM is never given up on, only asked about less often.
CLEANUP_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (60.0, 300.0, 900.0, 3600.0)
# How long a claimed cleanup attempt may stay in flight before another worker
# may reclaim the row. Sits comfortably above any bounded provider terminate
# call, so only a worker that has actually died — or wedged past its own
# timeout — is fenced out. The clock decides WHEN a row is reclaimable; the
# token decides whether the old holder's late write still counts (it does not).
CLEANUP_INFLIGHT_DEADLINE_SECONDS = 600.0
_CLEANUP_ATTEMPT_PREFIX = "cleanup_attempt_"
_CLEANUP_INFLIGHT_PREFIX = "cleanup_inflight_"


def cleanup_attempt_phase(*, attempts: int) -> str:
    """The `phase` marker of a PARKED cleanup_pending row: nobody holds it.

    Piggybacks the existing free-text phase column so the count is durable
    without a migration, and reads as a lifecycle phase to an operator.
    """
    return f"{_CLEANUP_ATTEMPT_PREFIX}{max(int(attempts), 1)}"


def new_cleanup_token() -> str:
    """A fresh ownership token for one cleanup attempt."""
    return secrets.token_hex(8)


def cleanup_inflight_phase(*, attempts: int, token: str) -> str:
    """The `phase` marker naming the worker that OWNS this cleanup attempt.

    An EXPLICIT marker, because a timestamp can only be guessed at: a worker
    that re-reads the row after the winner's claim sees a fresh `updated_at`
    and no way to tell the winner's own stamp from a settled row's. The marker
    says so outright — and the token in it is the fence every completion write
    CASes on, so a holder that was reclaimed for being over the deadline
    discovers it by failing that CAS rather than by settling a row it lost.
    """
    return f"{_CLEANUP_INFLIGHT_PREFIX}{max(int(attempts), 1)}:{token}"


def cleanup_attempts(*, phase: Any) -> int:
    """Attempts already made on a cleanup_pending row; 0 when unmarked.

    Reads both markers: an in-flight phase carries the attempt its holder took.
    """
    text = str(phase or "")
    for prefix in (_CLEANUP_ATTEMPT_PREFIX, _CLEANUP_INFLIGHT_PREFIX):
        if not text.startswith(prefix):
            continue
        try:
            return max(int(text[len(prefix):].split(":", 1)[0]), 0)
        except ValueError:
            return 0
    return 0


def cleanup_inflight_token(*, phase: Any) -> str:
    """The ownership token a phase carries, or "" when the row is parked."""
    text = str(phase or "")
    if not text.startswith(_CLEANUP_INFLIGHT_PREFIX):
        return ""
    return text.partition(":")[2]


def public_phase(*, phase: Any) -> str:
    """A phase safe to project: the in-flight ownership token never leaves the row."""
    text = str(phase or "")
    if not cleanup_inflight_token(phase=text):
        return text
    return cleanup_attempt_phase(attempts=cleanup_attempts(phase=text))


def cleanup_claim_expired(*, claimed_at: datetime | None, now: datetime) -> bool:
    """Whether an in-flight marker is old enough for another worker to reclaim.

    An unstamped marker has nothing proving it is fresh, so it is reclaimable —
    the same reading `cleanup_retry_due` gives a missing last-attempt clock.
    """
    if claimed_at is None:
        return True
    return claimed_at <= cleanup_claim_cutoff(now=now)


def cleanup_claim_cutoff(*, now: datetime) -> datetime:
    """The newest claim stamp already past the deadline.

    The reader's half of `cleanup_claim_expired`, expressed as a bound the
    reclaim's WHERE clause can carry — so the check the caller makes and the
    check the database re-makes are the same deadline, not two of them.
    """
    return now - timedelta(seconds=CLEANUP_INFLIGHT_DEADLINE_SECONDS)


@dataclass(frozen=True, slots=True)
class CleanupClaim:
    """Whether this worker owns the next cleanup attempt, and under which fence.

    ``phase`` is the exact in-flight marker the claim wrote; every write that
    finishes the attempt asserts it, so a holder fenced out by a stale-claim
    reclaim lands a no-op instead of a second settlement. It is empty when no
    fence applies — a row that is not parked has a single owner established
    elsewhere (a live job, the reaper's own re-read), so nothing to CAS on.
    """

    granted: bool
    token: str = ""
    attempts: int = 0
    phase: str = ""

    def __bool__(self) -> bool:
        return self.granted


CLEANUP_CLAIM_REFUSED = CleanupClaim(granted=False)
# Granted with no fence: the row was never parked, so no claim was needed.
CLEANUP_CLAIM_UNFENCED = CleanupClaim(granted=True)


def cleanup_retry_due(
    *, attempts: int, last_attempt_at: datetime | None, now: datetime
) -> bool:
    """Whether a cleanup_pending row's backoff window has elapsed."""
    if last_attempt_at is None:
        return True
    index = min(max(attempts, 1), len(CLEANUP_RETRY_BACKOFF_SECONDS)) - 1
    return (
        now - last_attempt_at
    ).total_seconds() >= CLEANUP_RETRY_BACKOFF_SECONDS[index]


def _safe_name(identity: str) -> str:
    """Filesystem-safe key/conn filename for a sandbox identity."""
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in identity) or "sandbox"


# Markers the in-sandbox rec.sh ForceCommand wrapper writes to the transcript:
#   command start: "[<ts>] $ <command>"
#   command exit:  "[<ts>] (exit <code>)"   (rc captured via PIPESTATUS[0])
# Parsing them lets `terminal` report a structured exit status, so an agent can
# tell when a command finished and whether it succeeded instead of busy-polling
# the transcript tail. Best-effort: a sandbox created before the marker landed,
# an empty log, or a read taken mid-command simply yields None / False.
_EXIT_MARKER_RE = re.compile(r"^\[([^\]]*)\] \(exit (-?\d+)\)[ \t]*$", re.MULTILINE)
_CMD_MARKER_RE = re.compile(r"^\[([^\]]*)\] \$ (.*)$", re.MULTILINE)
COMMAND_OUTPUT_TAIL_CHARS = 2000


def parse_terminal_snapshot(transcript: str) -> dict[str, Any]:
    """Extract structured status for the latest command in a transcript."""
    empty = {
        "command_id": None,
        "command": "",
        "started_at": None,
        "status": "unknown",
        "exit_code": None,
        "finished_at": None,
        "output_tail": "",
    }
    if not transcript:
        return empty
    commands = list(_CMD_MARKER_RE.finditer(transcript))
    if not commands:
        return empty
    command = commands[-1]
    started_at = command.group(1).strip() or None
    command_text = command.group(2).strip()
    exits_after_command = [
        match for match in _EXIT_MARKER_RE.finditer(transcript, command.end())
    ]
    exit_match = exits_after_command[0] if exits_after_command else None
    if exit_match is None:
        output = transcript[command.end():]
        exit_code = None
        finished_at = None
        status = "running"
    else:
        output = transcript[command.end():exit_match.start()]
        exit_code = int(exit_match.group(2))
        finished_at = exit_match.group(1).strip() or None
        status = "succeeded" if exit_code == 0 else "failed"
    command_key = f"{len(commands)}\0{started_at or ''}\0{command_text}"
    command_id = "cmd_" + hashlib.sha1(command_key.encode("utf-8")).hexdigest()[:12]
    return {
        "command_id": command_id,
        "command": command_text,
        "started_at": started_at,
        "status": status,
        "exit_code": exit_code,
        "finished_at": finished_at,
        "output_tail": output[-COMMAND_OUTPUT_TAIL_CHARS:].lstrip("\n"),
    }


def parse_terminal_markers(transcript: str) -> tuple[int | None, str | None, bool]:
    """Extract ``(last_exit_code, last_command_finished_at, command_running)``.

    ``command_running`` is True when the most recent command-start marker has no
    following exit marker — i.e. a command is still in flight. A transcript with
    no markers (old sandbox, empty log) degrades to ``(None, None, False)``.
    """
    if not transcript:
        return None, None, False
    last_exit_code: int | None = None
    last_finished_at: str | None = None
    last_exit_end = -1
    exits = list(_EXIT_MARKER_RE.finditer(transcript))
    if exits:
        last = exits[-1]
        last_exit_code = int(last.group(2))
        last_finished_at = last.group(1).strip() or None
        last_exit_end = last.end()
    cmds = list(_CMD_MARKER_RE.finditer(transcript))
    command_running = bool(cmds and cmds[-1].start() > last_exit_end)
    return last_exit_code, last_finished_at, command_running


def validate_request_inputs(
    *,
    gpu: str | None,
    cpu: float | None,
    memory: int | None,
    time_limit: int | None,
    configurable_resources: bool = True,
) -> tuple[str | None, float, int, int]:
    norm_gpu: str | None = None
    if gpu not in (None, ""):
        norm_gpu = str(gpu).upper()
        # On configurable backends (Modal) `gpu` names a concrete attachable GPU,
        # so validate it against the supported set. On bundled-hardware backends
        # (Lambda Labs) `gpu` is only a free-form filter over live instance types
        # — the real selector is `instance_type` — so accept any string here and
        # let capacity resolution reject a genuinely unavailable choice.
        if configurable_resources and norm_gpu not in VALID_GPUS:
            raise ValidationError(
                f"invalid gpu: {gpu}; allowed: {', '.join(sorted(VALID_GPUS))}"
            )
    norm_cpu = float(cpu) if cpu is not None else DEFAULT_CPU
    if norm_cpu <= 0:
        raise ValidationError("cpu must be positive")
    norm_memory = int(memory) if memory is not None else DEFAULT_MEMORY_MB
    if norm_memory < 512:
        raise ValidationError("memory must be at least 512 (MiB)")
    norm_time = int(time_limit) if time_limit is not None else DEFAULT_TIME_LIMIT_SECONDS
    if norm_time < MIN_TIME_LIMIT_SECONDS or norm_time > MAX_TIME_LIMIT_SECONDS:
        raise ValidationError(
            f"time_limit must be between {MIN_TIME_LIMIT_SECONDS} and {MAX_TIME_LIMIT_SECONDS} seconds"
        )
    return norm_gpu, norm_cpu, norm_memory, norm_time


def parse_iso(value: Any) -> datetime | None:
    return _parse_iso(value)

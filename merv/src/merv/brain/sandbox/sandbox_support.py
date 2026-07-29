# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Pure Sandbox constants, value types, and projection helpers."""

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
# Unconfirmed destruction remains visible and retryable, never terminal.
CLEANUP_PENDING_STATUS = "cleanup_pending"
TERMINAL_SANDBOX_STATUSES: frozenset[str] = frozenset({"terminated", "failed"})
UNREUSABLE_SANDBOX_STATUSES: frozenset[str] = TERMINAL_SANDBOX_STATUSES | {
    CLEANUP_PENDING_STATUS
}
MAX_TIME_LIMIT_SECONDS = 24 * 60 * 60
MIN_TIME_LIMIT_SECONDS = 60
DEFAULT_TIME_LIMIT_SECONDS = 3600
DEFAULT_CPU = 2.0
DEFAULT_MEMORY_MB = 8192

# Stay below the common ~60s MCP timeout.
DEFAULT_REQUEST_WAIT_SECONDS = 45.0
# Server cap for callers whose transport allows a long receipt poll.
RUNS_WAIT_CAP_SECONDS = 300.0
RUNS_WAIT_POLL_SECONDS = 5.0
# Reconcile old provisioning rows whose local job vanished.
DEFAULT_STALE_PROVISION_SECONDS = 15 * 60.0
# Lambda cold boots make tighter polling wasteful.
POLL_AFTER_SECONDS = 30
METRICS_CACHE_TTL_SECONDS = 2.0
# VM providers such as Lambda lack Modal's server-side lifetime enforcement.
DEFAULT_REAPER_INTERVAL_SECONDS = 30.0
DEFAULT_SANDBOX_IDLE_SECONDS = 3600.0
# The final backoff repeats forever while a VM may still bill.
CLEANUP_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (60.0, 300.0, 900.0, 3600.0)
# Longer than bounded provider termination; a replacement token fences late writes.
CLEANUP_INFLIGHT_DEADLINE_SECONDS = 600.0
_CLEANUP_ATTEMPT_PREFIX = "cleanup_attempt_"
_CLEANUP_INFLIGHT_PREFIX = "cleanup_inflight_"


def cleanup_attempt_phase(*, attempts: int) -> str:
    """Store retry count in the existing phase column while nobody holds it."""
    return f"{_CLEANUP_ATTEMPT_PREFIX}{max(int(attempts), 1)}"


def new_cleanup_token() -> str:
    return secrets.token_hex(8)


def cleanup_inflight_phase(*, attempts: int, token: str) -> str:
    """Encode ownership so reclaimed workers fail their completion CAS."""
    return f"{_CLEANUP_INFLIGHT_PREFIX}{max(int(attempts), 1)}:{token}"


def cleanup_attempts(*, phase: Any) -> int:
    """Read retry count from parked or in-flight markers."""
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
    """Treat an unstamped claim as reclaimable because freshness is unproven."""
    if claimed_at is None:
        return True
    return claimed_at <= cleanup_claim_cutoff(now=now)


def cleanup_claim_cutoff(*, now: datetime) -> datetime:
    """Return the same reclaim boundary used by both reader and database CAS."""
    return now - timedelta(seconds=CLEANUP_INFLIGHT_DEADLINE_SECONDS)


@dataclass(frozen=True, slots=True)
class CleanupClaim:
    """Cleanup ownership plus the exact phase fence completion must assert."""

    granted: bool
    token: str = ""
    attempts: int = 0
    phase: str = ""

    def __bool__(self) -> bool:
        return self.granted


CLEANUP_CLAIM_REFUSED = CleanupClaim(granted=False)
CLEANUP_CLAIM_UNFENCED = CleanupClaim(granted=True)


def cleanup_retry_due(
    *, attempts: int, last_attempt_at: datetime | None, now: datetime
) -> bool:
    if last_attempt_at is None:
        return True
    index = min(max(attempts, 1), len(CLEANUP_RETRY_BACKOFF_SECONDS)) - 1
    return (
        now - last_attempt_at
    ).total_seconds() >= CLEANUP_RETRY_BACKOFF_SECONDS[index]


def _safe_name(identity: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in identity) or "sandbox"


# ``rec.sh`` writes:
#   command start: "[<ts>] $ <command>"
#   command exit:  "[<ts>] (exit <code>)"   (rc captured via PIPESTATUS[0])
# Old or partial transcripts degrade to unknown.
_EXIT_MARKER_RE = re.compile(r"^\[([^\]]*)\] \(exit (-?\d+)\)[ \t]*$", re.MULTILINE)
_CMD_MARKER_RE = re.compile(r"^\[([^\]]*)\] \$ (.*)$", re.MULTILINE)
COMMAND_OUTPUT_TAIL_CHARS = 2000


def parse_terminal_snapshot(transcript: str) -> dict[str, Any]:
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
    """Report a running command when its latest start has no following exit."""
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
        # Modal accepts a GPU directly; bundled-hardware providers select by SKU.
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

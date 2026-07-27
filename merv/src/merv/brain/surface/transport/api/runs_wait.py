"""Auth-exempt run-wait streams: one GET that blocks until a run ends.

``GET /wait/{sandbox_uid}/{label}/{sig}`` is how an agent on ANY platform waits
for detached ``merv_run`` work without holding a credential: the URL is the
capability, its tag is an HMAC over exactly the (sandbox_uid, label) it names,
and what it reveals is exactly what a waiter needs — that the run ended, and
how. Outputs, logs and receipts stay behind the authenticated tools.

Nothing is stored for a wait. Validity is re-derived from rows that already
exist, and only from BRAIN clocks (when this process last mirrored the run,
when it last read the box), never from a timestamp the sandbox wrote.

Every terminal answer — 410, 429, resolution, hold cap, a failure mid-stream —
ends with one ``MERV_RUNS_WAIT <state> <label>`` line, so a caller that only
greps for that prefix always learns what happened. Invalid signature, unknown
sandbox and lapsed validity are one answer on purpose: the endpoint is an
unauthenticated lookup, and three answers would be an oracle.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse

from ....kernel.env import env_int
from ....kernel.secret_tokens import wait_signature_matches
from ....kernel.utils import parse_iso
from ....sandbox.facade import SandboxFacade


WAIT_ROUTE_PREFIX = "/wait/"
MAX_STREAMS_ENV_VAR = "MERV_WAIT_MAX_STREAMS"
# How many waits this process will hold at once. Each is an idle async task
# plus one bounded DB read every poll, so the ceiling is about the reads and
# the sockets, not memory.
DEFAULT_MAX_STREAMS = 64
# One URL is one waiter re-arming, plus at most one overlap while it does.
PER_SIGNATURE_STREAMS = 2
# Reject-path budget: refused requests still cost an HMAC and a socket.
BUCKET_CAPACITY = 128.0
BUCKET_REFILL_PER_SECOND = 32.0

WAIT_POLL_SECONDS = 5.0
WAIT_HEARTBEAT_SECONDS = 20.0
# An hour, then the caller re-arms with the same URL. Bounded so a forgotten
# waiter cannot hold a slot for the life of the process.
WAIT_HOLD_CAP_SECONDS = 3600.0
# A terminal run answers for six hours after this process last saw it, so a
# waiter that reconnects late still gets its answer instead of a mystery.
WAIT_TERMINAL_GRACE_SECONDS = 6 * 3600.0
# Nothing stays valid past the paid-for lease plus a day. This is the ceiling
# for rows nothing ever reconciled; it moves with sandbox.extend by itself.
WAIT_LEASE_CEILING_SECONDS = 24 * 3600.0
# Reconciliation while holding: the observer's own freshness stamp dedupes
# concurrent waiters on one box, and its permit pool caps the real reads.
WAIT_OBSERVE_MAX_AGE_SECONDS = 75.0
WAIT_OBSERVE_ACQUIRE_SECONDS = 10.0
WAIT_OBSERVE_WORKERS = 6

_MAX_ECHO_CHARS = 128
# The wire format is line-oriented and the label arrives as caller-controlled
# path text, so anything outside merv_run's own charset — a percent-encoded
# newline above all — could otherwise forge a second protocol line.
_UNSAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9._-]")


class _TokenBucket:
    """Process-wide entry budget, so the reject path has a cost ceiling too."""

    def __init__(self, *, capacity: float, per_second: float) -> None:
        self.capacity = capacity
        self.per_second = per_second
        self._tokens = capacity
        self._stamp = time.monotonic()
        self._guard = threading.Lock()

    def take(self) -> bool:
        with self._guard:
            now = time.monotonic()
            self._tokens = min(
                self.capacity, self._tokens + (now - self._stamp) * self.per_second
            )
            self._stamp = now
            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True


class _StreamAdmission:
    """Concurrent-hold accounting: one ceiling process-wide, one per URL."""

    def __init__(self) -> None:
        self.limit = env_int(MAX_STREAMS_ENV_VAR, DEFAULT_MAX_STREAMS, strict=False)
        self.per_signature = PER_SIGNATURE_STREAMS
        self._held = 0
        self._by_signature: dict[str, int] = {}
        self._guard = threading.Lock()

    def acquire(self, *, signature: str) -> bool:
        with self._guard:
            if self._held >= self.limit:
                return False
            if self._by_signature.get(signature, 0) >= self.per_signature:
                return False
            self._held += 1
            self._by_signature[signature] = self._by_signature.get(signature, 0) + 1
            return True

    def release(self, *, signature: str) -> None:
        with self._guard:
            self._held = max(self._held - 1, 0)
            remaining = self._by_signature.get(signature, 0) - 1
            if remaining > 0:
                self._by_signature[signature] = remaining
            else:
                self._by_signature.pop(signature, None)

    def held(self) -> int:
        with self._guard:
            return self._held


_BUCKET = _TokenBucket(
    capacity=BUCKET_CAPACITY, per_second=BUCKET_REFILL_PER_SECOND
)
_ADMISSION = _StreamAdmission()
# Its own pool: a receipt read blocks its thread for up to the acquire budget,
# and starving the request threadpool of workers would stall unrelated routes.
_OBSERVE_POOL = ThreadPoolExecutor(
    max_workers=WAIT_OBSERVE_WORKERS, thread_name_prefix="merv-wait-observe"
)


@dataclass(frozen=True, slots=True)
class _Verdict:
    """hold, done (with the two facts a waiter gets), or gone."""

    state: str
    status: str = ""
    exit_code: str = "none"


_HOLD = _Verdict("hold")
_GONE = _Verdict("gone")


def _echo(label: str) -> str:
    """The label as it goes back on the wire, forced into merv_run's charset."""
    return _UNSAFE_LABEL_RE.sub("_", label)[:_MAX_ECHO_CHARS] or "_"


def _line(state: str, label: str, extra: str = "") -> str:
    tail = f" {extra}" if extra else ""
    return f"MERV_RUNS_WAIT {state} {label}{tail}\n"


def _plain(body: str, *, status_code: int) -> Response:
    return Response(
        content=body,
        status_code=status_code,
        media_type="text/plain",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _verdict(*, facts: dict | None, now: datetime) -> _Verdict:
    """Whether this URL still answers, and with what, from brain clocks only."""
    if facts is None:
        return _GONE
    lease = parse_iso(facts.get("expires_at"))
    if lease is not None and now > lease + timedelta(
        seconds=WAIT_LEASE_CEILING_SECONDS
    ):
        return _GONE
    if not facts.get("present"):
        # Registration lag: merv_run wrote its receipt and the mirror has not
        # read it yet. Only a live box can still be about to register one.
        return _HOLD if facts.get("sandbox_active") else _GONE
    status = str(facts.get("status") or "")
    if status == "running":
        return _HOLD
    observed = parse_iso(facts.get("observed_at"))
    if observed is not None and now > observed + timedelta(
        seconds=WAIT_TERMINAL_GRACE_SECONDS
    ):
        return _GONE
    exit_code = facts.get("exit_code")
    return _Verdict(
        "done",
        status=status,
        exit_code="none" if exit_code is None else str(int(exit_code)),
    )


def _observe(*, sandboxes: SandboxFacade, sandbox_uid: str) -> None:
    """One reconciliation attempt for a held run. Blocking; never raises.

    A skipped cycle is exactly a failed read: the mirror stays as it was and
    the next poll asks again.
    """
    try:
        row = sandboxes.repository.get_by_uid(sandbox_uid=sandbox_uid)
        sandboxes.runs_observer.observe(
            row=row,
            max_age_seconds=WAIT_OBSERVE_MAX_AGE_SECONDS,
            acquire_timeout=WAIT_OBSERVE_ACQUIRE_SECONDS,
        )
    except Exception:  # noqa: BLE001 — a wait must outlive one bad read
        return


def build_router(
    *, sandboxes: SandboxFacade, secret: bytes | None = None
) -> APIRouter:
    """Mount the wait route. A composition that names no durable key still
    signs, but its URLs die with the process; both deployments name one."""
    key = secret if secret else secrets.token_bytes(32)
    api_router = APIRouter()

    @api_router.get(WAIT_ROUTE_PREFIX + "{sandbox_uid}/{label}/{sig}")
    def wait_for_run(sandbox_uid: str, label: str, sig: str) -> Response:
        echo = _echo(label)
        if not _BUCKET.take():
            return _plain(_line("poll_error", echo, "rate_limited"), status_code=429)
        # The MAC decides before anything is looked up: a forged tag costs one
        # HMAC and touches no row, so this endpoint is not a sandbox probe.
        if not wait_signature_matches(
            key=key, sandbox_uid=sandbox_uid, label=label, presented=sig
        ):
            return _plain(_line("no_such_run", echo), status_code=410)
        if not _ADMISSION.acquire(signature=sig):
            return _plain(_line("poll_error", echo, "rate_limited"), status_code=429)
        try:
            facts = sandboxes.runs_ledger.wait_facts(
                sandbox_uid=sandbox_uid, label=label
            )
            opening = _verdict(facts=facts, now=datetime.now(tz=UTC))
        except Exception:  # noqa: BLE001 — the slot must not leak on a bad read
            _ADMISSION.release(signature=sig)
            return _plain(_line("poll_error", echo), status_code=500)
        if opening.state == "gone":
            _ADMISSION.release(signature=sig)
            return _plain(_line("no_such_run", echo), status_code=410)

        async def hold(first: dict | None):
            loop = asyncio.get_running_loop()
            started = loop.time()
            beat = started - WAIT_HEARTBEAT_SECONDS
            facts = first
            try:
                while True:
                    verdict = _verdict(facts=facts, now=datetime.now(tz=UTC))
                    if verdict.state == "done":
                        yield _line(
                            "done",
                            echo,
                            f"status={verdict.status} exit_code={verdict.exit_code}",
                        )
                        return
                    if verdict.state == "gone":
                        # Validity lapsed mid-hold; the headers are long gone,
                        # so the 410's grammar is all that is left to send.
                        yield _line("no_such_run", echo)
                        return
                    now = loop.time()
                    if now - started >= WAIT_HOLD_CAP_SECONDS:
                        yield _line("still_running", echo)
                        return
                    if now - beat >= WAIT_HEARTBEAT_SECONDS:
                        beat = now
                        # Never matches the MERV_RUNS_WAIT prefix, and the
                        # first one flushes the response past any proxy.
                        yield f"# waiting {int(now - started)}s\n"
                    try:
                        await asyncio.wait_for(
                            loop.run_in_executor(
                                _OBSERVE_POOL,
                                lambda: _observe(
                                    sandboxes=sandboxes, sandbox_uid=sandbox_uid
                                ),
                            ),
                            timeout=WAIT_OBSERVE_ACQUIRE_SECONDS + 5.0,
                        )
                    except (asyncio.TimeoutError, RuntimeError):
                        pass  # a saturated pool must not stall the heartbeat
                    await asyncio.sleep(WAIT_POLL_SECONDS)
                    facts = sandboxes.runs_ledger.wait_facts(
                        sandbox_uid=sandbox_uid, label=label
                    )
            except Exception:  # noqa: BLE001 — say so rather than die silent
                yield _line("poll_error", echo)
            finally:
                # Also the disconnect path: a closed generator lands here, and
                # a slot a vanished client still holds is a slot lost forever.
                _ADMISSION.release(signature=sig)

        return StreamingResponse(
            hold(facts),
            media_type="text/plain",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return api_router


__all__ = ["build_router"]

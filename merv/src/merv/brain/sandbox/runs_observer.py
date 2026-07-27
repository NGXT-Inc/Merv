"""The one gateway this process has to a sandbox's remote receipt reads.

Four paths want `.runs` receipts refreshed — the sandbox.runs long-poll, the
daemon sweep, the last read every teardown takes before the VM dies, and the
idle reaper's SAN-07 veto — and each used to reach for the box on its own
schedule. Two pollers on one sandbox meant two ~30s SSH reads of the same
directory and two racing mirror writes behind them.

They all call `RunsObserver.observe` now. One lock per sandbox_uid (which
serializes the ledger's upsert too, since the read owns the lock while it
writes), a stamp of the last SUCCESSFUL read so a caller that would accept a
few-seconds-old mirror is never charged for a fresh one, and a single
process-wide permit pool bounding how many boxes are being read at once.

How long a caller may wait for a permit is the caller's own business: the
reaper's single-threaded loop waits as long as it takes because a wrong answer
there destroys live work, while a request path carries a finite budget and
gives up rather than delay a billing stop — an observation skipped this way is
byte-for-byte a failed SSH read, which leaves the row unstamped and reads
downstream as `unknown`, never `lost`.

Blocking by construction with every wait explicitly bounded, so an async
caller can hand `observe` to a dedicated executor without changing anything
here.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..kernel.env import env_int
from .repository import SandboxRepository
from .sandbox_runs import SandboxRunLedger
from .sandbox_support import ACTIVE_SANDBOX_STATUSES


CONCURRENCY_ENV_VAR = "MERV_RUNS_OBSERVER_CONCURRENCY"
# How many sandboxes may be read at once, process-wide. A receipt read is a
# blocking SSH round-trip that can hold its thread for ~30s, so this is the
# ceiling on how much of a request threadpool the fleet can occupy at once.
DEFAULT_OBSERVER_CONCURRENCY = 4
# How stale a mirror the daemon sweep accepts before re-reading. Its own read
# would be one tick old by the time the idle judgment downstream uses it, and
# that judgment measures idleness in minutes.
DAEMON_SWEEP_MAX_AGE_SECONDS = 10.0
# How long a confirmed release waits for a read slot before terminating without
# the observation. Bounded because the bill stops at the terminate, not at the
# read, and observation contention must never hold that up.
RELEASE_OBSERVE_ACQUIRE_SECONDS = 20.0
# Gates outlive their sandbox; these bound the registry without ever dropping
# one somebody is standing on.
_GATE_REGISTRY_LIMIT = 512
_GATE_IDLE_SECONDS = 300.0


@dataclass(slots=True)
class _UidGate:
    """One sandbox's read lock, its last successful read, and who wants it."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    observed_at: float = 0.0
    waiters: int = 0


class RunsObserver:
    """Sole entry point for pulling a sandbox's receipts over the wire."""

    def __init__(
        self,
        *,
        ledger: SandboxRunLedger,
        repository: SandboxRepository,
        concurrency: int | None = None,
    ) -> None:
        self.ledger = ledger
        self.repository = repository
        permits = (
            int(concurrency)
            if concurrency is not None
            else env_int(
                CONCURRENCY_ENV_VAR, DEFAULT_OBSERVER_CONCURRENCY, strict=False
            )
        )
        self._permits = threading.BoundedSemaphore(max(permits, 1))
        self._gates: dict[str, _UidGate] = {}
        self._gates_guard = threading.Lock()

    # ---------- entry points ----------

    def observe(
        self,
        *,
        row: dict[str, Any],
        max_age_seconds: float,
        force: bool = False,
        acquire_timeout: float | None = None,
    ) -> bool:
        """Mirror one row's receipts, reusing a recent read when it will do.

        Returns `reconcile_row`'s verdict — True means this row's receipts are
        MIRRORED AND CURRENT — with two ways to answer without asking the box:
        a successful read younger than `max_age_seconds` (unless `force`), and
        a wait for the read slot that outlived the caller's budget, which
        answers False and stamps nothing so the caller's next tick retries.

        `acquire_timeout` None waits as long as it takes and is only for the
        reaper's own loop; a non-forced caller that names no budget is held to
        the freshness it asked for, since waiting longer than that buys it
        nothing its own next pass would not.
        """
        sandbox_uid = str(row.get("sandbox_uid") or "")
        # Rows the ledger would refuse to read never answer from the stamp: a
        # box that has gone terminal has no receipts left to be current about.
        if not sandbox_uid or row.get("status") not in ACTIVE_SANDBOX_STATUSES:
            return bool(self.ledger.reconcile_row(row=row))
        if acquire_timeout is None and not force:
            acquire_timeout = max(float(max_age_seconds), 0.0)
        deadline = (
            None if acquire_timeout is None else time.monotonic() + acquire_timeout
        )
        gate = self._gate(sandbox_uid=sandbox_uid)
        try:
            if not force and _is_fresh(gate=gate, max_age_seconds=max_age_seconds):
                return True
            if not _acquire(gate.lock, deadline=deadline):
                return False
            try:
                # Double-checked: whoever held the lock may have just read this
                # box, and their answer is this caller's answer too.
                if not force and _is_fresh(gate=gate, max_age_seconds=max_age_seconds):
                    return True
                if not _acquire(self._permits, deadline=deadline):
                    return False
                try:
                    # Still under the uid lock, so the mirror write inside the
                    # read is serialized against every other read of this box.
                    observed = bool(self.ledger.reconcile_row(row=row))
                finally:
                    self._permits.release()
                if observed:
                    gate.observed_at = time.monotonic()
                return observed
            finally:
                gate.lock.release()
        finally:
            self._release_gate(sandbox_uid=sandbox_uid, gate=gate)

    def observe_forced(
        self, *, row: dict[str, Any], acquire_timeout: float | None = None
    ) -> bool:
        """A read that must touch the box: pre-terminal, or the idle-reap veto.

        Never served from a stamp — a sandbox is about to be destroyed, or
        spared, on the strength of this answer, and the run that finished
        seconds ago exists only on the VM. Called while the row is STILL
        active; `reconcile_row` refuses anything else.

        Stamping is deliberately somebody else's separate step
        (`SandboxRunLedger.mark_final_observed`): a terminate can come back
        `maybe_alive` and leave the row running, and a stamp written on that
        attempt would still be sitting there when some later path takes the row
        terminal without reading anything — turning an unobserved outcome into
        a confident `lost`.
        """
        return self.observe(
            row=row,
            max_age_seconds=0.0,
            force=True,
            acquire_timeout=acquire_timeout,
        )

    def observe_live(
        self, *, max_age_seconds: float = DAEMON_SWEEP_MAX_AGE_SECONDS
    ) -> int:
        """Daemon sweep: refresh receipts for every running sandbox.

        Rows without runs cost one cheap remote listing (missing .runs dir is
        an empty answer). Returns how many rows answered.
        """
        answered = 0
        for row in self.repository.list_running_rows():
            try:
                if self.observe(row=row, max_age_seconds=max_age_seconds):
                    answered += 1
            except Exception:  # noqa: BLE001 — the reaper loop must never die
                continue
        return answered

    # ---------- per-uid gates ----------

    def _gate(self, *, sandbox_uid: str) -> _UidGate:
        with self._gates_guard:
            gate = self._gates.get(sandbox_uid)
            if gate is None:
                gate = _UidGate()
                self._gates[sandbox_uid] = gate
            gate.waiters += 1
            return gate

    def _release_gate(self, *, sandbox_uid: str, gate: _UidGate) -> None:
        with self._gates_guard:
            gate.waiters -= 1
            if len(self._gates) <= _GATE_REGISTRY_LIMIT:
                return
            # Only a gate nobody holds or waits on may be dropped: one uid means
            # one lock object, or two observers of a box would both read it.
            cutoff = time.monotonic() - _GATE_IDLE_SECONDS
            self._gates = {
                uid: item
                for uid, item in self._gates.items()
                if item.waiters > 0 or item.observed_at > cutoff
            }


def _is_fresh(*, gate: _UidGate, max_age_seconds: float) -> bool:
    # Only a successful read stamps, so a stamp inside the window IS the True
    # verdict the reader that wrote it earned.
    if max_age_seconds <= 0 or gate.observed_at <= 0:
        return False
    return (time.monotonic() - gate.observed_at) <= float(max_age_seconds)


def _acquire(
    primitive: threading.Lock | threading.BoundedSemaphore, *, deadline: float | None
) -> bool:
    """Take a lock or a permit, bounded by a monotonic deadline (None = wait)."""
    if deadline is None:
        return bool(primitive.acquire())
    return bool(primitive.acquire(timeout=max(deadline - time.monotonic(), 0.0)))


__all__ = [
    "DAEMON_SWEEP_MAX_AGE_SECONDS",
    "DEFAULT_OBSERVER_CONCURRENCY",
    "RELEASE_OBSERVE_ACQUIRE_SECONDS",
    "RunsObserver",
]

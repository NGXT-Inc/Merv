"""Remote receipts, transcripts, metrics, and their concurrency gates.

Receipt reads serialize per sandbox and share a process-wide permit pool.
Only successful reads earn a freshness stamp; a skipped or failed final read
therefore produces ``unknown``, never a false ``lost`` result.
"""

from __future__ import annotations

import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

from ..kernel.env import env_int
from ..kernel.ports.mgmt_keys import MgmtKeyStore
from ..kernel.secret_tokens import wait_url
from ..kernel.state.store import BaseStateStore, row_to_dict
from ..kernel.utils import NotFoundError, format_iso, now_iso, parse_iso
from .sandbox_backend import (
    SandboxBackend,
    TranscriptTail,
    qualified_row_sandbox_id,
)
from .storage import SandboxStorage
from .sandbox_support import (
    ACTIVE_SANDBOX_STATUSES,
    METRICS_CACHE_TTL_SECONDS,
)


CONCURRENCY_ENV_VAR = "MERV_RUNS_OBSERVER_CONCURRENCY"
# Receipt reads are blocking SSH calls, so cap their threadpool occupancy.
DEFAULT_OBSERVER_CONCURRENCY = 4
# Idle decisions work in minutes; the daemon may reuse a seconds-old mirror.
DAEMON_SWEEP_MAX_AGE_SECONDS = 10.0
# Receipt contention must not delay a confirmed release indefinitely.
RELEASE_OBSERVE_ACQUIRE_SECONDS = 20.0
# Never evict a gate while a caller holds or awaits it.
_GATE_REGISTRY_LIMIT = 512
_GATE_IDLE_SECONDS = 300.0


@dataclass(slots=True)
class _UidGate:
    lock: threading.Lock = field(default_factory=threading.Lock)
    observed_at: float = 0.0
    waiters: int = 0


class RunsObserver:
    """Sole entry point for pulling a sandbox's receipts over the wire."""

    def __init__(
        self,
        *,
        ledger: SandboxRunLedger,
        repository: SandboxStorage,
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
        """Mirror receipts, reusing a successful read within ``max_age_seconds``.

        A timeout returns False without stamping, so the next pass retries.
        ``acquire_timeout=None`` is reserved for safety-critical reaper reads.
        """
        sandbox_uid = str(row.get("sandbox_uid") or "")
        # Terminal rows cannot reuse an old "current" stamp.
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
                # The previous lock holder may have refreshed the mirror.
                if not force and _is_fresh(gate=gate, max_age_seconds=max_age_seconds):
                    return True
                if not _acquire(self._permits, deadline=deadline):
                    return False
                try:
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
        """Read the box for a pre-terminal or idle-reap decision.

        This bypasses freshness. Final-observation stamping remains separate
        because a failed termination must not turn a later unread outcome into
        ``lost``.
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
        """Refresh receipts for every running sandbox."""
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


class SandboxRunLedger:
    """Owns every read and write of the `sandbox_runs` table."""

    def __init__(
        self,
        *,
        store: BaseStateStore,
        repository: SandboxStorage,
        backend: SandboxBackend,
        mgmt_keys: MgmtKeyStore,
    ) -> None:
        self.store = store
        self.repository = repository
        self.backend = backend
        self.mgmt_keys = mgmt_keys

    # ---------- reconcile (box filesystem -> table) ----------

    def reconcile_row(self, *, row: dict[str, Any]) -> bool:
        """Refresh one receipt mirror.

        ``None`` or a failed mirror write leaves existing records untouched and
        returns False; the idle reaper must treat either as possible work.
        """
        if row.get("status") not in ACTIVE_SANDBOX_STATUSES:
            return False
        sandbox_uid = str(row.get("sandbox_uid") or "")
        sandbox_id = str(row.get("sandbox_id") or "")
        if not sandbox_uid or not sandbox_id:
            return False
        try:
            addressed_id = qualified_row_sandbox_id(backend=self.backend, row=row)
            listing = self.backend.read_runs(
                sandbox_id=addressed_id,
                workdir=str(row.get("workdir") or ""),
                ssh_host=str(row.get("ssh_host") or ""),
                ssh_port=int(row.get("ssh_port") or 0),
                ssh_user=str(row.get("ssh_user") or ""),
                key_path=str(self.mgmt_keys.key_path(sandbox_uid=sandbox_uid)),
            )
        except Exception:  # noqa: BLE001 — observation is best-effort
            return False
        if listing is None:
            return False
        if listing:
            try:
                self._record(row=row, listing=listing)
            except (
                Exception
            ):  # noqa: BLE001 — an unmirrored receipt is not an absent one
                return False
        return True

    def mark_final_observed(
        self,
        *,
        sandbox_uid: str,
        expected_project_id: str,
        expected_phase: str = "",
    ) -> None:
        """Stamp a fenced final read; only this evidence permits ``lost``."""
        if not sandbox_uid:
            return
        self.repository.stamp_runs_observed(
            sandbox_uid=sandbox_uid,
            expected_project_id=expected_project_id,
            expected_phase=expected_phase,
        )

    def _record(self, *, row: dict[str, Any], listing: list[dict[str, Any]]) -> None:
        sandbox_uid = str(row.get("sandbox_uid") or "")
        now = now_iso()
        with self.store.transaction() as conn:
            for run in listing:
                label = str(run.get("label") or "")
                if not label:
                    continue
                exit_code = run.get("exit_code")
                existing = conn.execute(
                    "SELECT exit_code, finished_event_emitted FROM sandbox_runs "
                    "WHERE sandbox_uid = ? AND label = ?",
                    (sandbox_uid, label),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO sandbox_runs (
                          sandbox_uid, label, command, pid, exit_code,
                          started_at, finished_at, first_seen_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sandbox_uid,
                            label,
                            str(run.get("command") or ""),
                            run.get("pid"),
                            exit_code,
                            str(run.get("started_at") or ""),
                            str(run.get("finished_at") or ""),
                            now,
                            now,
                        ),
                    )
                elif existing["exit_code"] is None:
                    # Finished records never regress.
                    conn.execute(
                        """
                        UPDATE sandbox_runs
                        SET command = ?, pid = ?, exit_code = ?, finished_at = ?,
                            updated_at = ?
                        WHERE sandbox_uid = ? AND label = ?
                        """,
                        (
                            str(run.get("command") or ""),
                            run.get("pid"),
                            exit_code,
                            str(run.get("finished_at") or ""),
                            now,
                            sandbox_uid,
                            label,
                        ),
                    )
                if exit_code is None:
                    continue
                emitted = conn.execute(
                    "UPDATE sandbox_runs SET finished_event_emitted = 1 "
                    "WHERE sandbox_uid = ? AND label = ? "
                    "AND finished_event_emitted = 0",
                    (sandbox_uid, label),
                )
                if int(getattr(emitted, "rowcount", 0)) != 1:
                    continue
                self.store.record_event(
                    conn=conn,
                    project_id=str(row.get("project_id") or ""),
                    event_type="run.finished",
                    target_type="sandbox",
                    target_id=str(row.get("experiment_id") or sandbox_uid),
                    payload={
                        "sandbox_uid": sandbox_uid,
                        "label": label,
                        "exit_code": int(exit_code),
                        "finished_at": str(run.get("finished_at") or ""),
                    },
                )

    # ---------- reads ----------

    def has_running_runs(
        self, *, sandbox_uid: str, fresh_since: datetime | None = None
    ) -> bool:
        """Whether a fresh receipt records work that gauges might miss.

        Receipts can veto idle reap, never hard expiry.
        """
        if not sandbox_uid:
            return False
        clause = "" if fresh_since is None else " AND r.updated_at >= ?"
        params: list[Any] = [sandbox_uid]
        if fresh_since is not None:
            params.append(format_iso(fresh_since))
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                f"""
                SELECT 1 FROM sandbox_runs r
                WHERE r.sandbox_uid = ? AND r.exit_code IS NULL{clause}
                LIMIT 1
                """,
                params,
            ).fetchone()
        return row is not None

    def records_for_sandbox(self, *, sandbox_uid: str) -> list[dict[str, Any]]:
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                """
                SELECT r.*, s.status AS sandbox_status,
                       s.runs_final_observed_at AS runs_final_observed_at
                FROM sandbox_runs r
                JOIN sandboxes s ON s.sandbox_uid = r.sandbox_uid
                WHERE r.sandbox_uid = ?
                ORDER BY r.first_seen_at, r.label
                """,
                (sandbox_uid,),
            ).fetchall()
            return [row_to_dict(row=item) or {} for item in rows]

    def wait_facts(self, *, sandbox_uid: str, label: str) -> dict[str, Any] | None:
        """Return the narrow, non-secret projection allowed by a signed wait URL.

        ``present`` distinguishes registration lag from a known run.
        """
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                """
                SELECT s.status AS sandbox_status,
                       s.expires_at AS expires_at,
                       s.runs_final_observed_at AS runs_final_observed_at,
                       r.label AS run_label,
                       r.exit_code AS exit_code,
                       r.updated_at AS run_updated_at
                FROM sandboxes s
                LEFT JOIN sandbox_runs r
                  ON r.sandbox_uid = s.sandbox_uid AND r.label = ?
                WHERE s.sandbox_uid = ?
                """,
                (label, sandbox_uid),
            ).fetchone()
        if row is None:
            return None
        facts = row_to_dict(row=row) or {}
        present = facts.get("run_label") is not None
        observed = str(facts.get("runs_final_observed_at") or "")
        updated = str(facts.get("run_updated_at") or "")
        return {
            "present": present,
            "status": run_status(facts) if present else "",
            "exit_code": facts.get("exit_code"),
            # Same-format UTC strings preserve time ordering.
            "observed_at": max(observed, updated),
            "sandbox_active": facts.get("sandbox_status") in ACTIVE_SANDBOX_STATUSES,
            "expires_at": str(facts.get("expires_at") or ""),
        }

    def records_for_experiment(self, *, experiment_id: str) -> list[dict[str, Any]]:
        """Include detached and terminated sandboxes for durable history."""
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                """
                SELECT r.*, s.status AS sandbox_status,
                       s.runs_final_observed_at AS runs_final_observed_at
                FROM sandbox_runs r
                JOIN sandboxes s ON s.sandbox_uid = r.sandbox_uid
                WHERE r.sandbox_uid IN (
                  SELECT DISTINCT sandbox_uid FROM sandbox_attachments
                  WHERE experiment_id = ?
                )
                ORDER BY r.first_seen_at, r.label
                """,
                (experiment_id,),
            ).fetchall()
            return [row_to_dict(row=item) or {} for item in rows]

    # ---------- views ----------

    def nudge_line(self, *, sandbox_uid: str) -> str | None:
        """Render from the mirror without adding remote I/O to other reads."""
        records = self.records_for_sandbox(sandbox_uid=sandbox_uid)
        if not records:
            return None
        now = datetime.now(tz=UTC)
        live = [r for r in records if run_status(r) == "running"]
        finished = [r for r in records if run_status(r) == "finished"]
        lost = [r for r in records if run_status(r) == "lost"]
        unknown = [r for r in records if run_status(r) == "unknown"]
        parts: list[str] = []
        if live:
            shown = ", ".join(
                f"{r.get('label')} {_age(r.get('started_at'), now)}" for r in live[:3]
            )
            more = f", +{len(live) - 3} more" if len(live) > 3 else ""
            parts.append(f"{len(live)} live ({shown}{more})")
        if finished:
            shown = ", ".join(
                f"{r.get('label')}, exit {r.get('exit_code')}" for r in finished[:3]
            )
            more = f", +{len(finished) - 3} more" if len(finished) > 3 else ""
            parts.append(f"{len(finished)} finished ({shown}{more})")
        if lost:
            parts.append(f"{len(lost)} lost with the box")
        if unknown:
            parts.append(f"{len(unknown)} unknown (box died unread)")
        return "runs: " + " · ".join(parts) + " — sandbox.runs for detail"


def run_records_view(
    *,
    records: list[dict[str, Any]],
    experiment_id: str = "",
    sandbox_uid: str = "",
    base_url: str = "",
    wait_secret: bytes | None = None,
) -> dict[str, Any]:
    """Compact run view; wait URLs require both a public base and signing key."""
    multi_sandbox = len({str(r.get("sandbox_uid") or "") for r in records}) > 1
    runs: list[dict[str, Any]] = []
    live = finished = lost = unknown = 0
    for record in records:
        status = run_status(record)
        label = str(record.get("label") or "")
        uid = str(record.get("sandbox_uid") or "")
        view: dict[str, Any] = {
            "label": record.get("label"),
            "status": status,
            "started_at": record.get("started_at") or None,
            "log": f".runs/{record.get('label')}/log.txt",
        }
        if base_url and wait_secret and label and uid:
            # Bind each signature to its row; experiment views span sandboxes.
            view["wait_url"] = wait_url(
                base_url=base_url, key=wait_secret, sandbox_uid=uid, label=label
            )
        if status == "running":
            live += 1
        elif status == "finished":
            finished += 1
            view["exit_code"] = record.get("exit_code")
            view["finished_at"] = record.get("finished_at") or None
        elif status == "lost":
            lost += 1
        else:
            unknown += 1
        if multi_sandbox:
            view["sandbox_uid"] = record.get("sandbox_uid")
        runs.append(view)
    out: dict[str, Any] = {}
    if experiment_id:
        out["experiment_id"] = experiment_id
    if sandbox_uid:
        out["sandbox_uid"] = sandbox_uid
    out.update({"runs": runs, "live": live, "finished": finished})
    if lost:
        out["lost"] = lost
    if unknown:
        out["unknown"] = unknown
        out["unknown_hint"] = (
            "The box died before its receipts could be read, so these runs have "
            "no known outcome — not a failure. Treat them as unresolved."
        )
    if not runs:
        out["hint"] = (
            "No merv_run receipts. Launch anything long with "
            "`merv_run <label> -- <command>` on the sandbox: it survives SSH "
            "disconnects and reports its exit code here."
        )
    return out


def run_status(record: dict[str, Any]) -> str:
    """Interpret unfinished terminal runs as ``lost`` only after a final read."""
    if record.get("exit_code") is not None:
        return "finished"
    if record.get("sandbox_status") in ACTIVE_SANDBOX_STATUSES:
        return "running"
    return "lost" if record.get("runs_final_observed_at") else "unknown"


def _age(started_at: Any, now: datetime) -> str:
    started = parse_iso(started_at)
    if started is None:
        return "?"
    seconds = max(int((now - started).total_seconds()), 0)
    hours, minutes = seconds // 3600, (seconds % 3600) // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


class SandboxMetrics:
    def __init__(
        self,
        *,
        repository: SandboxStorage,
        backend: SandboxBackend,
        mgmt_keys: MgmtKeyStore,
    ) -> None:
        self.repository = repository
        self.backend = backend
        self.mgmt_keys = mgmt_keys
        self._cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._lock = threading.Lock()

    def sample_metrics(
        self,
        *,
        experiment_id: str,
        project_id: str | None = None,
        sandbox_uid: str | None = None,
    ) -> dict[str, Any]:
        try:
            row = self.repository.fetch_scoped(
                experiment_id=experiment_id,
                project_id=project_id,
                sandbox_uid=sandbox_uid,
            )
        except NotFoundError:
            return {
                "experiment_id": experiment_id,
                "sandbox_uid": sandbox_uid or "",
                "status": "none",
                "available": False,
                "metrics": None,
            }
        resolved_experiment_id = experiment_id or str(row.get("experiment_id") or "")
        status = row.get("status")
        sandbox_id = str(row.get("sandbox_id") or "")
        base: dict[str, Any] = {
            "experiment_id": resolved_experiment_id,
            "sandbox_uid": row.get("sandbox_uid", ""),
            "sandbox_id": sandbox_id,
            "status": status,
            "reserved": {
                "gpu": row.get("gpu") or "",
                "cpu": row.get("cpu"),
                "memory_mib": row.get("memory"),
                "instance_type": row.get("instance_type") or "",
                "region": row.get("region") or "",
            },
        }
        if status not in ACTIVE_SANDBOX_STATUSES or not sandbox_id:
            return {**base, "available": False, "metrics": None}
        metrics = self._sample_cached(
            experiment_id=resolved_experiment_id, sandbox_id=sandbox_id, row=row
        )
        return {
            **base,
            "available": metrics is not None,
            "metrics": metrics,
            "sampled_at": now_iso(),
        }

    def _sample_cached(
        self, *, experiment_id: str, sandbox_id: str, row: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            addressed_id = qualified_row_sandbox_id(backend=self.backend, row=row)
        except Exception:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(addressed_id)
            if cached is not None and now - cached[0] < METRICS_CACHE_TTL_SECONDS:
                return cached[1]
        try:
            metrics = self.backend.sample_metrics(
                sandbox_id=addressed_id,
                ssh_host=str(row.get("ssh_host") or ""),
                ssh_port=int(row.get("ssh_port") or 0),
                ssh_user=str(row.get("ssh_user") or ""),
                key_path=str(self._mgmt_key_path(row=row)),
            )
        except Exception:  # noqa: BLE001 - metrics are best-effort
            metrics = None
        with self._lock:
            self._cache[addressed_id] = (time.monotonic(), metrics)
        return metrics

    def _mgmt_key_path(self, *, row: dict[str, Any]) -> Any:
        return self.mgmt_keys.key_path(sandbox_uid=str(row.get("sandbox_uid") or ""))


# Coalesce viewers while retaining near-live output.
DEFAULT_TTL_SECONDS = 2.0
DEFAULT_MAX_ENTRIES = 256


@dataclass
class _Entry:
    tail: TranscriptTail
    stored_at: float


class TranscriptCache:
    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self._clock = clock or time.monotonic
        self._entries: dict[str, _Entry] = {}
        # Guard shared state but keep remote reads outside the lock.
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get_or_read(
        self,
        *,
        sandbox_id: str,
        read: Callable[[], TranscriptTail],
        since: int | None = None,
        fresh: bool = False,
    ) -> TranscriptTail:
        """Read on miss, expiry, ``fresh``, or a cursor at the true byte end."""
        if not sandbox_id:
            self.misses += 1
            return read()
        now = self._clock()
        with self._lock:
            entry = self._entries.get(sandbox_id)
            fresh_window = (
                entry is not None and (now - entry.stored_at) < self.ttl_seconds
            )
            cursor_in_cache = (
                entry is not None
                and since is not None
                and int(since) < entry.tail.total_bytes
            )
            servable = (
                entry is not None
                and not fresh
                and fresh_window
                and (since is None or cursor_in_cache)
            )
            if servable:
                assert entry is not None
                self.hits += 1
                return entry.tail
            self.misses += 1
        tail = read()
        with self._lock:
            self._store(sandbox_id=sandbox_id, tail=tail, now=now)
        return tail

    def invalidate(self, *, sandbox_id: str) -> None:
        with self._lock:
            self._entries.pop(sandbox_id, None)

    def _store(self, *, sandbox_id: str, tail: TranscriptTail, now: float) -> None:
        if sandbox_id not in self._entries and len(self._entries) >= self.max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k].stored_at)
            self._entries.pop(oldest, None)
        self._entries.pop(sandbox_id, None)
        self._entries[sandbox_id] = _Entry(tail=tail, stored_at=now)


__all__ = [
    "DAEMON_SWEEP_MAX_AGE_SECONDS",
    "DEFAULT_OBSERVER_CONCURRENCY",
    "RELEASE_OBSERVE_ACQUIRE_SECONDS",
    "RunsObserver",
    "SandboxMetrics",
    "SandboxRunLedger",
    "TranscriptCache",
    "run_records_view",
    "run_status",
]

"""Durable tool-call ledger: one row per call and per pre-dispatch refusal.

Sizes, digests, and outcomes ONLY. The in-memory rings keep serving the debug
UI the raw request/response it drills into; this table exists so agent friction
— retry loops, gate bounces, poll churn, per-tool latency and context bloat —
survives a restart, and it must never grow into a second payload store
(BACKEND_AUDIT §15.2).

Every write is fail-safe: a ledger failure is counted and announced through
``on_failure``, never raised into the call it was observing.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .activity import args_digest, error_head, ledger_label, payload_chars, target_of
from .store import Connection
from ..env import env_int
from ..request_context import current_request_context
from ..utils import format_iso, now_iso

TOOL_CALL_RETENTION_DAYS_ENV_VAR = "MERV_TOOL_CALL_RETENTION_DAYS"
DEFAULT_RETENTION_DAYS = 30
# One DELETE removes at most this many rows, so retention never holds the write
# lock for an unbounded span; one sweep runs at most this many of them, so a
# backlog is actually cleared instead of merely reported as `more`.
PRUNE_BATCH_ROWS = 20_000
PRUNE_MAX_BATCHES = 50
# A telemetry row may never make the call it observes wait. If the writer is
# contended past this, the row is dropped and counted — the alternative is a
# tool call paying the store's ten-second SQLite busy timeout for a log line.
LEDGER_LOCK_TIMEOUT_SECONDS = 0.25
LEDGER_BUSY_TIMEOUT_MS = 250

_STATUSES = frozenset({"ok", "error", "rejected"})


class LedgerBusy(RuntimeError):
    """The writer was contended, so the row was dropped rather than waited on."""


class LedgerConnections(Protocol):
    """The single store capability an append-only ledger needs."""

    def connect(self) -> Connection: ...


class DroppedRowSink(Protocol):
    """Told when a row could not be written, so no drop is ever silent."""

    def __call__(self, error: str) -> None: ...


class ToolCallLedger:
    """Append-only writer + retention sweep for the ``tool_calls`` table."""

    def __init__(
        self,
        *,
        store: LedgerConnections,
        retention_days: int | None = None,
        env: Mapping[str, str] | None = None,
        on_failure: DroppedRowSink | None = None,
    ) -> None:
        self._store = store
        configured = (
            int(retention_days)
            if retention_days is not None
            else env_int(
                TOOL_CALL_RETENTION_DAYS_ENV_VAR,
                DEFAULT_RETENTION_DAYS,
                env=env,
                strict=False,
            )
        )
        # A zero or negative horizon would delete the ledger it is protecting.
        self.retention_days = max(1, configured)
        self._on_failure = on_failure
        self.failures = 0
        # Re-entrant: _write holds it across _connection(), which takes it again
        # to register the handle it just opened.
        self._lock = threading.RLock()
        self._local = threading.local()
        self._open: list[Connection] = []

    def record(
        self,
        *,
        tool: str = "",
        source: str = "",
        status: str = "ok",
        duration_ms: int = 0,
        arguments: dict[str, Any] | None = None,
        result: Any | None = None,
        error: str = "",
        error_code: str = "",
        project_id: str = "",
    ) -> None:
        """Persist one call outcome. Never raises — this is telemetry."""
        try:
            self._insert(
                tool=tool,
                source=source,
                status=status if status in _STATUSES else "error",
                duration_ms=duration_ms,
                arguments=arguments or {},
                result=result,
                error=error,
                error_code=error_code,
                project_id=project_id,
            )
        except Exception as exc:  # noqa: BLE001 -- a dropped row is not a failed call
            self.failures += 1
            if self._on_failure is not None:
                with suppress(Exception):
                    self._on_failure(error_head(error=str(exc)) or type(exc).__name__)

    def reject(
        self,
        *,
        tool: str = "",
        source: str = "",
        error_code: str = "",
        error: str = "",
        project_id: str = "",
        duration_ms: int = 0,
    ) -> None:
        """Ledger a refusal that never reached the dispatcher."""
        self.record(
            tool=tool,
            source=source,
            status="rejected",
            duration_ms=duration_ms,
            error=error,
            error_code=error_code,
            project_id=project_id,
        )

    def prune(
        self, *, now: datetime | None = None, max_batches: int = PRUNE_MAX_BATCHES
    ) -> dict[str, Any]:
        """Delete rows past the retention horizon, reporting what happened.

        Batches until the horizon is clear or the iteration bound is spent: one
        20k batch per sweep cannot outrun a call rate that mints more rows than
        that in a day. A failed sweep says so (``ok`` False) instead of
        reporting zero deleted — a silent 0 is indistinguishable from a healthy
        no-op (audit OPS-03).
        """
        cutoff = format_iso(
            (now or datetime.now(tz=UTC)) - timedelta(days=self.retention_days)
        )
        deleted = 0
        more = False
        try:
            for _ in range(max(1, int(max_batches))):
                batch, more = self._delete_before(cutoff=cutoff)
                deleted += batch
                if not more:
                    break
        except Exception as exc:  # noqa: BLE001 -- one sweep must not abort the pass
            self.failures += 1
            return {
                "deleted": deleted,
                "ok": False,
                "cutoff": cutoff,
                "error": error_head(error=str(exc)) or type(exc).__name__,
            }
        return {"deleted": deleted, "ok": True, "cutoff": cutoff, "more": more}

    def close(self) -> None:
        """Release every cached connection. Called on composition shutdown."""
        with self._lock:
            handles, self._open = self._open, []
            self._local = threading.local()
        for conn in handles:
            with suppress(Exception):  # a foreign thread's SQLite handle
                conn.close()

    def _insert(
        self,
        *,
        tool: str,
        source: str,
        status: str,
        duration_ms: int,
        arguments: dict[str, Any],
        result: Any | None,
        error: str,
        error_code: str,
        project_id: str,
    ) -> None:
        context = current_request_context()
        target_type, target_id = target_of(arguments)
        scope = project_id or (
            str(arguments.get("project_id") or "") if isinstance(arguments, dict) else ""
        )
        # Mirrors the in-memory ring exactly: an error's received size is the
        # error text the caller got back, not a result it never saw.
        received = (
            len(error or "") if status != "ok" else payload_chars(value=result)
        )
        # Every label is capped and scrubbed HERE, at the one writer, so no
        # transport can put a multi-kilobyte or token-bearing value into an
        # indexed column by forgetting to sanitize its own call site.
        self._write(
            """
            INSERT INTO tool_calls
              (ts, request_id, principal_id, tool, source, project_id,
               target_type, target_id, status, error_code, error_head,
               duration_ms, sent_chars, received_chars, args_digest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                ledger_label(context.request_id),
                ledger_label(context.principal_id),
                ledger_label(tool),
                ledger_label(source),
                ledger_label(scope),
                ledger_label(target_type or ""),
                ledger_label(target_id or ""),
                status,
                ledger_label(error_code),
                error_head(error=error),
                int(duration_ms or 0),
                payload_chars(value=arguments),
                int(received),
                args_digest(arguments=arguments),
            ),
        )

    def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        """One append, serialized and time-bounded.

        Append-only, so a plain connection is enough: no read-then-write means
        no need for the store's single-writer transaction on every call. The
        lock keeps concurrent rows from contending for the database write lock
        with each other, and its timeout is the promise that a contended ledger
        drops a row instead of delaying the call it was observing.
        """
        if not self._lock.acquire(timeout=LEDGER_LOCK_TIMEOUT_SECONDS):
            raise LedgerBusy("tool-call ledger writer is busy")
        try:
            conn = self._connection()
            try:
                conn.execute(sql, params)
                conn.commit()
            except Exception:
                self._discard()  # a failed handle may be a dead one
                raise
        finally:
            self._lock.release()

    def _connection(self) -> Connection:
        """This thread's cached connection, opened once and reused.

        Cached per THREAD rather than per instance because rows are written
        from the HTTP threadpool and the reaper thread, and a SQLite handle may
        only be used on the thread that opened it. What the database feels is
        the same thing either way: one connection per worker instead of a fresh
        connect, insert, commit, and close on every single row.
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = self._store.connect()
        with suppress(Exception):  # dialects without the pragma keep their own
            conn.execute(f"PRAGMA busy_timeout = {LEDGER_BUSY_TIMEOUT_MS}")
        self._local.conn = conn
        with self._lock:
            self._open.append(conn)
        return conn

    def _discard(self) -> None:
        """Drop a connection that just failed so the next row reconnects."""
        conn = getattr(self._local, "conn", None)
        self._local.conn = None
        if conn is None:
            return
        with self._lock, suppress(ValueError):
            self._open.remove(conn)
        with suppress(Exception):
            conn.close()

    def _delete_before(self, *, cutoff: str) -> tuple[int, bool]:
        """Delete one batch; report what it removed and whether more remain.

        Runs OUTSIDE the writer lock: a 20k-row DELETE holding it would drop
        every concurrent tool-call row for the duration of the sweep.
        """
        conn = self._connection()
        # Bound the sweep by id rather than by a LIMIT on DELETE, which neither
        # dialect supports portably. The count comes from the same subquery, so
        # it is exactly what the DELETE below removes: the batch is the first
        # `PRUNE_BATCH_ROWS` expired ids in order, so no expired row sits under
        # the boundary without being in it.
        row = conn.execute(
            """
            SELECT MAX(id) AS boundary, COUNT(*) AS expiring FROM (
              SELECT id FROM tool_calls WHERE ts < ? ORDER BY id LIMIT ?
            ) AS batch
            """,
            (cutoff, PRUNE_BATCH_ROWS),
        ).fetchone()
        boundary = int((row["boundary"] if row else None) or 0)
        deleted = int((row["expiring"] if row else 0) or 0)
        if boundary <= 0 or deleted <= 0:
            return 0, False
        conn.execute(
            "DELETE FROM tool_calls WHERE id <= ? AND ts < ?", (boundary, cutoff)
        )
        conn.commit()
        # `more` is the state of the table, not the size of the batch: exactly
        # PRUNE_BATCH_ROWS expired rows with none behind them reports False. A
        # short batch proves the horizon is clear without asking again.
        if deleted < PRUNE_BATCH_ROWS:
            return deleted, False
        remaining = conn.execute(
            "SELECT id FROM tool_calls WHERE ts < ? LIMIT 1", (cutoff,)
        ).fetchone()
        return deleted, remaining is not None


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "PRUNE_BATCH_ROWS",
    "PRUNE_MAX_BATCHES",
    "TOOL_CALL_RETENTION_DAYS_ENV_VAR",
    "LedgerBusy",
    "ToolCallLedger",
]

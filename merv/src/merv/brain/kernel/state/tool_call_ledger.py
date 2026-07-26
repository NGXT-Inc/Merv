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

from collections.abc import Mapping
from contextlib import closing, suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .activity import args_digest, error_head, payload_chars, target_of
from .store import Connection
from ..env import env_int
from ..request_context import current_request_context
from ..utils import format_iso, now_iso

TOOL_CALL_RETENTION_DAYS_ENV_VAR = "MERV_TOOL_CALL_RETENTION_DAYS"
DEFAULT_RETENTION_DAYS = 30
# One sweep deletes at most this many rows, so retention can never hold the
# write lock for an unbounded span. `more` in the outcome says work remains.
PRUNE_BATCH_ROWS = 20_000

_STATUSES = frozenset({"ok", "error", "rejected"})


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

    def prune(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Delete rows past the retention horizon, reporting what happened.

        A failed sweep says so (``ok`` False) instead of reporting zero deleted
        — a silent 0 is indistinguishable from a healthy no-op (audit OPS-03).
        """
        cutoff = format_iso(
            (now or datetime.now(tz=UTC)) - timedelta(days=self.retention_days)
        )
        try:
            deleted = self._delete_before(cutoff=cutoff)
        except Exception as exc:  # noqa: BLE001 -- one sweep must not abort the pass
            self.failures += 1
            return {
                "deleted": 0,
                "ok": False,
                "cutoff": cutoff,
                "error": error_head(error=str(exc)) or type(exc).__name__,
            }
        return {
            "deleted": deleted,
            "ok": True,
            "cutoff": cutoff,
            "more": deleted >= PRUNE_BATCH_ROWS,
        }

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
        # Append-only, so a plain connection is enough: no read-then-write means
        # no need for the store's single-writer transaction on every call.
        with closing(self._store.connect()) as conn:
            conn.execute(
                """
                INSERT INTO tool_calls
                  (ts, request_id, principal_id, tool, source, project_id,
                   target_type, target_id, status, error_code, error_head,
                   duration_ms, sent_chars, received_chars, args_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_iso(),
                    context.request_id,
                    context.principal_id,
                    tool,
                    source,
                    scope,
                    target_type or "",
                    target_id or "",
                    status,
                    error_code,
                    error_head(error=error),
                    int(duration_ms or 0),
                    payload_chars(value=arguments),
                    int(received),
                    args_digest(arguments=arguments),
                ),
            )
            conn.commit()

    def _delete_before(self, *, cutoff: str) -> int:
        with closing(self._store.connect()) as conn:
            # Bound the sweep by id rather than by a LIMIT on DELETE, which
            # neither dialect supports portably.
            row = conn.execute(
                """
                SELECT MAX(id) AS boundary FROM (
                  SELECT id FROM tool_calls WHERE ts < ? ORDER BY id LIMIT ?
                ) AS expiring
                """,
                (cutoff, PRUNE_BATCH_ROWS),
            ).fetchone()
            boundary = int((row["boundary"] if row else None) or 0)
            if boundary <= 0:
                return 0
            counted = conn.execute(
                "SELECT COUNT(*) AS expired FROM tool_calls WHERE id <= ? AND ts < ?",
                (boundary, cutoff),
            ).fetchone()
            deleted = int((counted["expired"] if counted else 0) or 0)
            conn.execute(
                "DELETE FROM tool_calls WHERE id <= ? AND ts < ?", (boundary, cutoff)
            )
            conn.commit()
            return deleted


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "TOOL_CALL_RETENTION_DAYS_ENV_VAR",
    "ToolCallLedger",
]

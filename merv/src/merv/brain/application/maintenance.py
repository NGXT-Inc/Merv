"""Cross-component housekeeping triggered by an operator or scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from ..kernel.ports.blob_store import ExpiringBlobStore
from ..kernel.ports.sandbox_lifecycle import (
    DEFAULT_STALE_PROVISION_DEADLINE_SECONDS,
    SandboxMaintenance,
)
from ..kernel.utils import format_iso


class ExpiringStorage(Protocol):
    """Heavy-storage capability needed by the maintenance use case."""

    def sweep_expired(self, *, now: str) -> int: ...


class PrunableLedger(Protocol):
    """Bounded retention sweep that reports its own outcome, not just a count."""

    def prune(self, *, now: datetime | None = None) -> dict[str, Any]: ...


SKIPPED_PRUNE: dict[str, Any] = {"deleted": 0, "ok": True, "skipped": True}


def _sweep_failure(exc: Exception) -> dict[str, Any]:
    return {"deleted": 0, "ok": False, "error": str(exc)[:200]}


@dataclass(frozen=True)
class CleanupReport:
    """Counts returned by one idempotent maintenance pass."""

    orphan_vms_reaped: int = 0
    blobs_swept: int = 0
    storage_objects_swept: int = 0
    stale_provisions_reaped: int = 0
    # Structured, not a count: a failed sweep must be distinguishable from a
    # sweep that legitimately deleted nothing (audit OPS-03).
    tool_calls_pruned: dict[str, Any] = field(
        default_factory=lambda: dict(SKIPPED_PRUNE)
    )
    oauth_clients_pruned: dict[str, Any] = field(
        default_factory=lambda: dict(SKIPPED_PRUNE)
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "orphan_vms_reaped": self.orphan_vms_reaped,
            "blobs_swept": self.blobs_swept,
            "storage_objects_swept": self.storage_objects_swept,
            "stale_provisions_reaped": self.stale_provisions_reaped,
            "tool_calls_pruned": dict(self.tool_calls_pruned),
            "oauth_clients_pruned": dict(self.oauth_clients_pruned),
        }


class CleanupService:
    """Run adapter-neutral, clock-injectable housekeeping sweeps."""

    def __init__(
        self,
        *,
        sandboxes: SandboxMaintenance,
        blobs: ExpiringBlobStore,
        storage: ExpiringStorage | None = None,
        tool_call_ledger: PrunableLedger | None = None,
        oauth_clients: PrunableLedger | None = None,
        stale_provision_deadline_seconds: float = (
            DEFAULT_STALE_PROVISION_DEADLINE_SECONDS
        ),
    ) -> None:
        self.sandboxes = sandboxes
        self.blobs = blobs
        self.storage = storage
        self.tool_call_ledger = tool_call_ledger
        self.oauth_clients = oauth_clients
        self.stale_provision_deadline_seconds = float(stale_provision_deadline_seconds)

    def run_all(self, *, now: datetime | None = None) -> CleanupReport:
        now_dt = now or datetime.now(tz=UTC)
        return CleanupReport(
            orphan_vms_reaped=self.sweep_orphan_vms(now=now_dt),
            blobs_swept=self.sweep_expired_blobs(now=now_dt),
            storage_objects_swept=self.sweep_expired_storage(now=now_dt),
            stale_provisions_reaped=self.sweep_stale_provisions(now=now_dt),
            tool_calls_pruned=self.prune_tool_calls(now=now_dt),
            oauth_clients_pruned=self.prune_oauth_clients(now=now_dt),
        )

    def sweep_orphan_vms(self, *, now: datetime | None = None) -> int:
        """Reconcile tracked running rows against their providers."""
        return self.sandboxes.reconcile_running_rows()

    def sweep_expired_blobs(self, *, now: datetime | None = None) -> int:
        """Best-effort TTL collection for submitted evidence bytes."""
        try:
            now_iso = format_iso(now or datetime.now(tz=UTC))
            return int(self.blobs.sweep_expired(now=now_iso))
        except Exception:  # noqa: BLE001 -- one GC adapter must not abort the pass
            return 0

    def sweep_expired_storage(self, *, now: datetime | None = None) -> int:
        """Best-effort, ledger-aware expiry for heavy storage."""
        if self.storage is None:
            return 0
        try:
            now_iso = format_iso(now or datetime.now(tz=UTC))
            return int(self.storage.sweep_expired(now=now_iso))
        except Exception:  # noqa: BLE001 -- one GC adapter must not abort the pass
            return 0

    def prune_tool_calls(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Bounded retention sweep over the durable tool-call ledger."""
        return self._prune(ledger=self.tool_call_ledger, now=now)

    def prune_oauth_clients(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Expire OAuth registrations that never authorized anything."""
        return self._prune(ledger=self.oauth_clients, now=now)

    def _prune(
        self, *, ledger: PrunableLedger | None, now: datetime | None
    ) -> dict[str, Any]:
        """Run one retention sweep and return its own report.

        A failure says ``ok`` False and names the error — it does NOT return
        zero, which would read as a healthy pass that found nothing (OPS-03).
        """
        if ledger is None:
            return dict(SKIPPED_PRUNE)
        try:
            return dict(ledger.prune(now=now))
        except Exception as exc:  # noqa: BLE001 -- one GC adapter must not abort the pass
            return _sweep_failure(exc)

    def sweep_stale_provisions(self, *, now: datetime | None = None) -> int:
        """Reap provider VMs stuck in any pre-running phase past the deadline."""
        return self.sandboxes.reap_stale_provisions(
            now=now or datetime.now(tz=UTC),
            deadline_seconds=self.stale_provision_deadline_seconds,
        )

"""Scheduling for expiration, observation, idle detection, and cleanup.

Lifecycle and acquisition own the decisions; this module owns cadence.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

from .sandbox_backend import SandboxBackend
from ..kernel.env import env_bool, env_float, env_raw
from ..kernel.ports.sandbox_lifecycle import ProvisionReaper
from .sandbox_lifecycle import SandboxLifecycle
from .storage import SandboxStorage
from .sandbox_support import (
    DEFAULT_REAPER_INTERVAL_SECONDS,
    DEFAULT_SANDBOX_IDLE_SECONDS,
    DEFAULT_STALE_PROVISION_DEADLINE_SECONDS,
)
from .sandbox_heartbeat import (
    RunActivityProbe,
    RunReceiptRefresh,
    SandboxHeartbeatMonitor,
    SandboxIdlePolicy,
)


# Low-frequency housekeeping shares the process's one timer.
DEFAULT_MAINTENANCE_INTERVAL_SECONDS = 3600.0

LOGGER = logging.getLogger(__name__)


class PeriodicMaintenance(Protocol):
    """Opaque housekeeping; the scheduler supplies only a clock."""

    def __call__(self) -> object: ...


class SandboxScheduler:
    def __init__(
        self,
        *,
        repository: SandboxStorage,
        backend: SandboxBackend,
        provisioner: ProvisionReaper,
        lifecycle: SandboxLifecycle,
        sample_metrics: Callable[..., dict[str, Any]] | None = None,
        reconcile_runs: Callable[[], int] | None = None,
        runs_active: RunActivityProbe | None = None,
        refresh_runs: RunReceiptRefresh | None = None,
        idle_policy: SandboxIdlePolicy | None = None,
        force_expiry_reaper: bool = False,
        periodic_maintenance: PeriodicMaintenance | None = None,
        maintenance_interval_seconds: float = DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
    ) -> None:
        self.repository = repository
        self.backend = backend
        self.provisioner = provisioner
        self.lifecycle = lifecycle
        self.reconcile_runs = reconcile_runs
        # Hosted control cannot disable expiry because it owns provider billing.
        self.force_expiry_reaper = bool(force_expiry_reaper)
        self.periodic_maintenance = periodic_maintenance
        self._maintenance_interval = float(maintenance_interval_seconds)
        self._maintenance_due = 0.0  # first tick runs it
        # Preserve callback failures for operator visibility.
        self.maintenance_failures = 0
        self.last_maintenance: object = None
        self.heartbeat = SandboxHeartbeatMonitor(
            repository=repository,
            sample_metrics=sample_metrics or (lambda **_kwargs: {}),
            reap_row=lifecycle.reap_row,
            policy=idle_policy,
            runs_active=runs_active,
            refresh_runs=refresh_runs,
        )
        self._reaper_stop = threading.Event()
        self.reaper_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._daemon_enabled():
            self.reaper_thread = threading.Thread(
                target=self._reaper_loop,
                name="sandbox-reaper",
                daemon=True,
            )
            self.reaper_thread.start()

    def stop(self) -> None:
        self._reaper_stop.set()
        if self.reaper_thread is not None:
            self.reaper_thread.join(timeout=2.0)

    # ---------- expiration reaper ----------

    def _daemon_enabled(self) -> bool:
        return (
            self._reaper_enabled()
            or self._idle_reap_threshold() > 0
            or self.periodic_maintenance is not None
        )

    def _reaper_enabled(self) -> bool:
        if not self.force_expiry_reaper:
            if not env_bool("RESEARCH_PLUGIN_SANDBOX_REAPER", default=True):
                return False
        return self.backend.capabilities.enforce_expiry

    def _reaper_loop(self) -> None:
        interval = env_float(
            "RESEARCH_PLUGIN_SANDBOX_REAPER_INTERVAL",
            None,
            DEFAULT_REAPER_INTERVAL_SECONDS,
        )
        stale_deadline = env_float(
            "RESEARCH_PLUGIN_SANDBOX_STALE_PROVISION_DEADLINE",
            None,
            DEFAULT_STALE_PROVISION_DEADLINE_SECONDS,
        )
        while not self._reaper_stop.wait(interval):
            self.sweep_once(stale_deadline_seconds=stale_deadline)

    def sweep_once(self, *, stale_deadline_seconds: float) -> None:
        """Run one tick in safety-critical dependency order."""
        expiry_enabled = self._reaper_enabled()
        with suppress(Exception):  # the reaper must never die
            if expiry_enabled:
                self.lifecycle.reap_expired()
        # Refresh receipts before idle judgment; gauges miss detached work.
        with suppress(Exception):  # the reaper must never die
            if self.reconcile_runs is not None:
                self.reconcile_runs()
        with suppress(Exception):  # the reaper must never die
            self.reap_idle(threshold_seconds=self._idle_reap_threshold())
        # Pre-running rows have no expiry, so reap wedged provisions separately.
        with suppress(Exception):  # the reaper must never die
            if expiry_enabled:
                self.provisioner.reap_stale_provisions(
                    now=datetime.now(tz=UTC),
                    deadline_seconds=stale_deadline_seconds,
                )
        # Parked rows may still bill; lifecycle owns their backoff.
        with suppress(Exception):  # the reaper must never die
            self.lifecycle.retry_cleanup_pending(now=datetime.now(tz=UTC))
        self.maintenance_tick(now=time.monotonic())

    def maintenance_tick(self, *, now: float) -> bool:
        """Run due housekeeping and retain failures for operator visibility."""
        if self.periodic_maintenance is None or now < self._maintenance_due:
            return False
        self._maintenance_due = now + self._maintenance_interval
        outcome: object
        try:
            outcome = self.periodic_maintenance()
        except Exception as exc:  # noqa: BLE001 -- the reaper must never die
            outcome = {"ok": False, "error": str(exc)}
        self.last_maintenance = outcome
        if isinstance(outcome, Mapping) and not outcome.get("ok", True):
            self.maintenance_failures += 1
            LOGGER.warning(
                "periodic maintenance reported a failure: %s",
                outcome.get("error") or outcome,
            )
        return True

    def _idle_reap_threshold(self) -> float:
        raw = env_raw("MERV_SANDBOX_IDLE_SECONDS")
        if raw == "":
            return 0.0
        threshold = env_float(
            "RESEARCH_PLUGIN_SANDBOX_IDLE_SECONDS",
            None,
            DEFAULT_SANDBOX_IDLE_SECONDS,
        )
        return threshold if threshold > 0 else 0.0

    def reap_idle(
        self,
        *,
        now: datetime | None = None,
        threshold_seconds: float | None = None,
    ) -> int:
        return self.heartbeat.reap_idle(
            now=now,
            threshold_seconds=(
                self._idle_reap_threshold()
                if threshold_seconds is None
                else float(threshold_seconds)
            ),
        )

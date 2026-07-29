# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Cadence for the Sandbox safety sweep."""

from __future__ import annotations

import logging
import threading
from typing import Protocol

from ..kernel.env import env_bool, env_float, env_raw
from ..kernel.ports.sandbox_lifecycle import (
    DEFAULT_STALE_PROVISION_DEADLINE_SECONDS,
)
from .models import (
    DEFAULT_REAPER_INTERVAL_SECONDS,
    DEFAULT_SANDBOX_IDLE_SECONDS,
)


LOGGER = logging.getLogger(__name__)


class ScheduledSweep(Protocol):
    def __call__(
        self,
        *,
        stale_deadline_seconds: float,
        expiry_enabled: bool,
        idle_threshold_seconds: float,
    ) -> None: ...


class SandboxScheduler:
    """Own cadence only; SandboxEngine owns sweep contents and ordering."""

    def __init__(
        self,
        *,
        sweep: ScheduledSweep,
        enforce_expiry: bool,
        force_expiry_reaper: bool = False,
    ) -> None:
        self._sweep = sweep
        self._enforce_expiry = bool(enforce_expiry)
        self.force_expiry_reaper = bool(force_expiry_reaper)
        self._stop = threading.Event()
        self.reaper_thread: threading.Thread | None = None

    def start(self) -> None:
        if self.reaper_thread is not None and self.reaper_thread.is_alive():
            return
        if not self._daemon_enabled():
            return
        self._stop.clear()
        self.reaper_thread = threading.Thread(
            target=self._loop,
            name="sandbox-reaper",
            daemon=True,
        )
        self.reaper_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.reaper_thread is not None:
            self.reaper_thread.join(timeout=2.0)

    def _daemon_enabled(self) -> bool:
        return (
            self._reaper_enabled()
            or self._idle_reap_threshold() > 0
        )

    def _reaper_enabled(self) -> bool:
        if not self.force_expiry_reaper and not env_bool(
            "RESEARCH_PLUGIN_SANDBOX_REAPER",
            default=True,
        ):
            return False
        return self._enforce_expiry

    def _loop(self) -> None:
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
        while not self._stop.wait(interval):
            self.sweep_once(stale_deadline_seconds=stale_deadline)

    def sweep_once(self, *, stale_deadline_seconds: float) -> None:
        try:
            self._sweep(
                stale_deadline_seconds=stale_deadline_seconds,
                expiry_enabled=self._reaper_enabled(),
                idle_threshold_seconds=self._idle_reap_threshold(),
            )
        except Exception:  # noqa: BLE001 -- the timer must survive a bad sweep
            LOGGER.exception("sandbox maintenance sweep failed")

    def _idle_reap_threshold(self) -> float:
        if env_raw("MERV_SANDBOX_IDLE_SECONDS") == "":
            return 0.0
        threshold = env_float(
            "RESEARCH_PLUGIN_SANDBOX_IDLE_SECONDS",
            None,
            DEFAULT_SANDBOX_IDLE_SECONDS,
        )
        return threshold if threshold > 0 else 0.0

"""Composition-owned Sandbox collaborators and background runtime."""

from __future__ import annotations

from dataclasses import dataclass

from ..kernel.env import env_float
from ..kernel.ports.mgmt_keys import MgmtKeyStore
from ..kernel.state.store import BaseStateStore
from .repository import SandboxRepository
from .runs_observer import RunsObserver
from .sandbox_backend import SandboxBackend
from .sandbox_daemons import SandboxDaemons
from .sandbox_lifecycle import SandboxLifecycle
from .sandbox_metrics import SandboxMetrics
from .sandbox_provisioner import SandboxProvisioner
from .sandbox_runs import SandboxRunLedger
from .sandbox_support import DEFAULT_STALE_PROVISION_SECONDS
from .transcript_cache import TranscriptCache


@dataclass(frozen=True, slots=True)
class SandboxRuntime:
    repository: SandboxRepository
    metrics: SandboxMetrics
    runs: SandboxRunLedger
    runs_observer: RunsObserver
    lifecycle: SandboxLifecycle
    provisioner: SandboxProvisioner
    daemons: SandboxDaemons
    transcripts: TranscriptCache

    def start(self) -> None:
        self.daemons.start()

    def shutdown(self) -> None:
        self.daemons.stop()
        self.provisioner.shutdown()


def build_sandbox_runtime(
    *,
    store: BaseStateStore,
    backend: SandboxBackend,
    mgmt_keys: MgmtKeyStore,
    stale_provision_seconds: float | None = None,
    force_expiry_reaper: bool = False,
) -> SandboxRuntime:
    """Construct the runtime without starting any thread."""
    repository = SandboxRepository(store=store)
    metrics = SandboxMetrics(repository=repository, backend=backend, mgmt_keys=mgmt_keys)
    runs = SandboxRunLedger(
        store=store,
        repository=repository,
        backend=backend,
        mgmt_keys=mgmt_keys,
    )
    # The process's one gateway to remote receipt reads: every path that wants
    # a box asked goes through it, so a sandbox is read once per window no
    # matter how many callers want the answer.
    runs_observer = RunsObserver(ledger=runs, repository=repository)
    lifecycle = SandboxLifecycle(
        repository=repository,
        backend=backend,
        mgmt_keys=mgmt_keys,
    )
    provisioner = SandboxProvisioner(
        repository=repository,
        backend=backend,
        lifecycle=lifecycle,
        stale_provision_seconds=env_float(
            "RESEARCH_PLUGIN_SANDBOX_STALE",
            stale_provision_seconds,
            DEFAULT_STALE_PROVISION_SECONDS,
        ),
    )
    lifecycle.job_probe = provisioner.job_is_live
    lifecycle.observe_runs = runs_observer.observe_forced
    lifecycle.stamp_runs_observed = runs.mark_final_observed
    daemons = SandboxDaemons(
        repository=repository,
        backend=backend,
        provisioner=provisioner,
        lifecycle=lifecycle,
        sample_metrics=metrics.sample_metrics,
        reconcile_runs=runs_observer.observe_live,
        runs_active=runs.has_running_runs,
        refresh_runs=runs_observer.observe_forced,
        force_expiry_reaper=force_expiry_reaper,
    )
    return SandboxRuntime(
        repository=repository,
        metrics=metrics,
        runs=runs,
        runs_observer=runs_observer,
        lifecycle=lifecycle,
        provisioner=provisioner,
        daemons=daemons,
        transcripts=TranscriptCache(),
    )


__all__ = ["SandboxRuntime", "build_sandbox_runtime"]

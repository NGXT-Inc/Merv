"""The DataPlaneWorker interface and its local-mode implementation.

Every local-IO duty of the sandbox stack routes through this seam (cloud plan
§3.1): workspace folders, SSH keypairs and conn files, the initial rsync push,
sync pulls, dashboard tunnels, and the pulled-``mlflow.db`` metrics fallback.
Control-plane code (registry, provisioner, facade verbs) calls the interface;
``LocalDataPlaneWorker`` binds it to this machine by wrapping the existing
machinery. In split mode the same duties become the daemon's task loop
(Phase 8).

Interim duty (Phases 3–4, plan §3.1): ``read_transcript``/``sample_metrics``
stay where they are — backend reads keyed by the worker-held user key — until
Phase 5's management-key switch makes them control-feasible.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from ..execution.ssh_rsync import SshRsyncSyncer
from ..execution.sync_dirs import (
    remote_experiment_dir,
    remote_root_of,
    remote_sessions_dir,
)
from ..execution.types import ProvisionedSandbox, SandboxBackend
from ..services.metrics_archive import MetricsArchive, snapshot_mlflow_db
from ..services.sandbox_conn import SandboxConnFiles
from ..services.sandbox_dashboards import DashboardTunnels
from ..services.sandbox_support import (
    ACTIVE_SANDBOX_STATUSES,
    DEFAULT_INITIAL_PUSH_ATTEMPTS,
    DEFAULT_INITIAL_PUSH_RETRY_SECONDS,
    env_float,
)
from ..workspace import LocalWorkspace
from .state import SandboxLocalState


# (attempt, attempts) — progress hook for the initial-push retry loop, so the
# provisioner can surface "waiting for remote workspace" without the worker
# knowing about provision rows.
OnPushRetry = Callable[[int, int], None]


class DataPlaneWorker(Protocol):
    """Local-IO duties the control plane is never allowed to perform itself."""

    workspace: LocalWorkspace
    metrics_archive: MetricsArchive

    def ensure_workspace(self, *, experiment_id: str, name: str = "") -> Path: ...

    def local_experiment_dir(self, *, experiment_id: str, name: str = "") -> Path: ...

    def repo_relative(self, path: str | Path) -> str: ...

    def ensure_keypair(self, *, experiment_id: str) -> tuple[str, Path]: ...

    def key_path(self, *, experiment_id: str) -> Path: ...

    def remove_conn_file(self, *, experiment_id: str) -> None: ...

    def sandbox_enrichment(
        self, *, row: dict[str, Any], name: str = ""
    ) -> dict[str, Any]: ...

    def push_initial(
        self,
        *,
        experiment_id: str,
        name: str = "",
        provisioned: ProvisionedSandbox,
        on_retry: OnPushRetry | None = None,
    ) -> dict[str, Any]: ...

    def sync_pull(
        self, *, row: dict[str, Any], name: str = "", skip_if_busy: bool = False
    ) -> dict[str, Any]: ...

    def final_pull(
        self, *, row: dict[str, Any], name: str = "", deadline: float | None = None
    ) -> dict[str, Any]: ...

    def ensure_local_dashboards(self, *, row: dict[str, Any]) -> dict[str, Any]: ...

    def merge_local_dashboards(self, *, row: dict[str, Any]) -> dict[str, Any]: ...

    def stop_dashboards(self, *, sandbox_id: str = "") -> None: ...

    def pulled_mlflow_db_path(self, *, experiment_id: str, name: str = "") -> Path: ...

    def capture_metrics_fallback(
        self, *, experiment_id: str, name: str = ""
    ) -> dict[str, Any] | None: ...

    def set_event_sink(self, emit_event: Callable[..., None]) -> None: ...


class LocalDataPlaneWorker:
    """Local-mode worker: today's sandbox IO machinery behind the seam.

    Wraps ``SandboxConnFiles`` (keys, dispatcher, conn files),
    ``SshRsyncSyncer`` (push/pull), ``DashboardTunnels`` (ssh -L pool), and
    ``MetricsArchive``; machine-local sandbox facts (key path, local sync dir,
    loopback dashboard URLs) persist in ``SandboxLocalState``, never in
    cloud-bound rows.
    """

    def __init__(
        self,
        *,
        workspace: LocalWorkspace,
        backend: SandboxBackend,
        rsync_syncer: SshRsyncSyncer | None = None,
    ) -> None:
        self.workspace = workspace
        self.rsync_syncer = rsync_syncer or SshRsyncSyncer()
        keys_dir = workspace.research_dir / "sandboxes" / "keys"
        self._conn = SandboxConnFiles(repo_root=workspace.repo_root, keys_dir=keys_dir)
        self.state = SandboxLocalState(
            db_path=workspace.research_dir / "dataplane_state.sqlite"
        )
        self.metrics_archive = MetricsArchive(repo_root=workspace.repo_root)
        self.dashboards = DashboardTunnels(
            backend=backend,
            key_path=self.key_path,
            local_state=self.state,
        )
        # One rsync per experiment at a time; sync/release/reap contend.
        self._sync_locks: dict[str, threading.Lock] = {}
        self._sync_locks_lock = threading.Lock()

    # ---------- workspace ----------

    def ensure_workspace(self, *, experiment_id: str, name: str = "") -> Path:
        """Create the experiment's one local folder (its sandbox sync root).

        Local mode creates it eagerly at experiment.create; in split mode
        workspace creation becomes lazy on the first data-routed touch
        (plan §3.1) — this method is that touch point.
        """
        folder = self.workspace.experiment_dir(experiment_id=experiment_id, name=name)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def local_experiment_dir(self, *, experiment_id: str, name: str = "") -> Path:
        stored = self.state.load(experiment_id=experiment_id)["local_sync_dir"]
        if stored:
            return Path(stored)
        return self.workspace.experiment_dir(experiment_id=experiment_id, name=name)

    def repo_relative(self, path: str | Path) -> str:
        return self.workspace.relative(path)

    # ---------- keys / conn files ----------

    def ensure_keypair(self, *, experiment_id: str) -> tuple[str, Path]:
        public_key, key_path = self._conn.ensure_keypair(experiment_id=experiment_id)
        self.state.record(experiment_id=experiment_id, key_path=str(key_path))
        return public_key, key_path

    def key_path(self, *, experiment_id: str) -> Path:
        return self._conn.key_path(experiment_id=experiment_id)

    def remove_conn_file(self, *, experiment_id: str) -> None:
        self._conn.remove_conn(experiment_id=experiment_id)

    def sandbox_enrichment(
        self, *, row: dict[str, Any], name: str = ""
    ) -> dict[str, Any]:
        """The machine-local half of the agent view (plan §3.3).

        Writes/refreshes the conn file for a live row and renders the ssh
        command, raw command, key path, and local folder. The control plane
        merges this with the row facts; in split mode the daemon supplies it.
        """
        experiment_id = str(row.get("experiment_id") or "")
        key_path = self.key_path(experiment_id=experiment_id)
        live = bool(
            row.get("ssh_host")
            and row.get("ssh_port")
            and (row.get("status") or "none") in ACTIVE_SANDBOX_STATUSES
        )
        command = (
            self._conn.write_command_wrapper(row=row, key_path=key_path) if live else ""
        )
        raw_command = (
            self._conn.raw_ssh_command(row=row, key_path=key_path) if live else ""
        )
        return {
            "key_path": str(key_path),
            "command": command,
            "raw_command": raw_command,
            "local_dir": str(
                self.local_experiment_dir(experiment_id=experiment_id, name=name)
            ),
        }

    # ---------- rsync ----------

    def push_initial(
        self,
        *,
        experiment_id: str,
        name: str = "",
        provisioned: ProvisionedSandbox,
        on_retry: OnPushRetry | None = None,
    ) -> dict[str, Any]:
        local_dir = self.local_experiment_dir(experiment_id=experiment_id, name=name)
        local_dir.mkdir(parents=True, exist_ok=True)
        attempts = max(
            1,
            int(env_float(
                "RESEARCH_PLUGIN_SANDBOX_INITIAL_PUSH_ATTEMPTS",
                None,
                DEFAULT_INITIAL_PUSH_ATTEMPTS,
            )),
        )
        retry_seconds = env_float(
            "RESEARCH_PLUGIN_SANDBOX_INITIAL_PUSH_RETRY",
            None,
            DEFAULT_INITIAL_PUSH_RETRY_SECONDS,
        )
        result: dict[str, Any] | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = self.rsync_syncer.push_initial(
                    ssh_host=provisioned.ssh_host,
                    ssh_port=provisioned.ssh_port,
                    ssh_user=provisioned.ssh_user,
                    key_path=self.key_path(experiment_id=experiment_id),
                    remote_sync_dir=provisioned.sync_dir
                    or provisioned.workdir
                    or remote_experiment_dir(experiment_id=experiment_id, name=name),
                    local_sync_dir=local_dir,
                ).as_dict()
                break
            except Exception:  # noqa: BLE001 — first push races cloud-init; retry briefly
                if attempt >= attempts:
                    raise
                if on_retry is not None:
                    on_retry(attempt, attempts)
                time.sleep(retry_seconds)
        assert result is not None
        self.state.record(experiment_id=experiment_id, local_sync_dir=str(local_dir))
        return result

    def sync_pull(
        self, *, row: dict[str, Any], name: str = "", skip_if_busy: bool = False
    ) -> dict[str, Any]:
        experiment_id = str(row.get("experiment_id") or "")
        with self._sync_locks_lock:
            lock = self._sync_locks.setdefault(experiment_id, threading.Lock())
        acquired = lock.acquire(blocking=not skip_if_busy)
        if not acquired:
            return {
                "provider": "ssh_rsync",
                "skipped": "busy",
                "pulled": 0,
                "conflicts": 0,
                "local_dir": str(
                    self.local_experiment_dir(experiment_id=experiment_id, name=name)
                ),
            }
        try:
            local_dir = self.local_experiment_dir(
                experiment_id=experiment_id, name=name
            )
            remote_dir = str(
                row.get("sync_dir")
                or row.get("workdir")
                or remote_experiment_dir(experiment_id=experiment_id, name=name)
            )
            result = self.rsync_syncer.sync(
                ssh_host=str(row.get("ssh_host") or ""),
                ssh_port=int(row.get("ssh_port") or 0),
                ssh_user=str(row.get("ssh_user") or "root"),
                key_path=self.key_path(experiment_id=experiment_id),
                remote_sync_dir=remote_dir,
                local_sync_dir=local_dir,
                # Sandbox-authored telemetry (MLflow db, TB events, transcript)
                # lives outside the experiment folder and lands in a daemon-owned
                # local dir, keyed by sandbox id so each VM generation's history
                # is preserved. Legacy rows simply have nothing at this remote
                # path (their sessions ride inside the synced folder).
                remote_sessions_dir=remote_sessions_dir(
                    experiment_id=experiment_id, root=remote_root_of(remote_dir)
                ),
                local_sessions_dir=self.workspace.sessions_dir(
                    experiment_id=experiment_id,
                    sandbox_id=str(row.get("sandbox_id") or ""),
                ),
            ).as_dict()
            self.state.record(
                experiment_id=experiment_id, local_sync_dir=str(local_dir)
            )
            return result
        finally:
            lock.release()

    def final_pull(
        self, *, row: dict[str, Any], name: str = "", deadline: float | None = None
    ) -> dict[str, Any]:
        """Last pull before terminate (release today; the reaper in Phase 4).

        ``deadline`` and the unreachable-daemon parachute branch arrive with
        the task channel and management keys (plan Phases 4–5); locally the
        worker is by definition reachable, so this is a busy-skipping pull.
        """
        del deadline
        return self.sync_pull(row=row, name=name, skip_if_busy=True)

    # ---------- dashboards ----------

    def ensure_local_dashboards(self, *, row: dict[str, Any]) -> dict[str, Any]:
        return self.dashboards.ensure_local(row=row)

    def merge_local_dashboards(self, *, row: dict[str, Any]) -> dict[str, Any]:
        return self.dashboards.merged_row(row=row)

    def stop_dashboards(self, *, sandbox_id: str = "") -> None:
        self.dashboards.stop(sandbox_id=sandbox_id)

    # ---------- pulled-metrics fallback ----------

    def pulled_mlflow_db_path(self, *, experiment_id: str, name: str = "") -> Path:
        # The sandbox's MLflow backend store, as mirrored locally by the rsync
        # pull. Current layout: the daemon-owned sessions dir, one subdir per
        # sandbox generation — pick the most recently modified db. Legacy
        # layouts (sessions inside the synced folder) are checked as fallbacks
        # so pre-change experiments keep their lazy metrics backfill.
        sessions_base = self.workspace.sessions_dir(experiment_id=experiment_id)
        candidates = sorted(
            sessions_base.glob("*/mlflow.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0]
        local_dir = self.local_experiment_dir(experiment_id=experiment_id, name=name)
        for legacy in (
            local_dir / ".research_plugin_sessions" / experiment_id / "mlflow.db",
            local_dir / "synced" / ".research_plugin_sessions" / experiment_id / "mlflow.db",
        ):
            if legacy.exists():
                return legacy
        return sessions_base / "mlflow.db"

    def capture_metrics_fallback(
        self, *, experiment_id: str, name: str = ""
    ) -> dict[str, Any] | None:
        return snapshot_mlflow_db(
            self.pulled_mlflow_db_path(experiment_id=experiment_id, name=name)
        )

    # ---------- record-sink wiring ----------

    def set_event_sink(self, emit_event: Callable[..., None]) -> None:
        """Bind the control-plane event recorder (registry.emit_event).

        Data-plane work that deserves a record (a dashboard tunnel came up)
        reports through this hook; Phase 4's task channel formalizes it.
        """
        self.dashboards.emit_event = emit_event

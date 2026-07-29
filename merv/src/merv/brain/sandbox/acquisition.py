"""Provider acquisition threads and cooperative cancellation.

Rows remain ``provisioning`` until running state and spend generation publish
atomically; lifecycle owns every destructive outcome before that handoff.
"""

from __future__ import annotations

from contextlib import suppress
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .sandbox_backend import (
    BackendPermissionError,
    BackendUnavailableError,
    BackendValidationError,
    SandboxBackend,
    SandboxRequest,
)
from ..kernel.utils import iso_after, now_iso
from .sandbox_lifecycle import SandboxLifecycle
from .storage import SandboxStorage
from .sandbox_support import parse_iso


class _Canceled(Exception):
    """Raised inside a provisioning callback to abort acquire on release."""


@dataclass
class _ProvisionJob:
    thread: threading.Thread
    cancel: threading.Event
    done: threading.Event
    experiment_id: str
    sandbox_uid: str = ""


class SandboxAcquisition:
    """Owns in-flight provisioning job threads."""

    def __init__(
        self,
        *,
        repository: SandboxStorage,
        backend: SandboxBackend,
        lifecycle: SandboxLifecycle,
        stale_provision_seconds: float,
    ) -> None:
        self.repository = repository
        self.backend = backend
        self.lifecycle = lifecycle
        self.stale_provision_seconds = stale_provision_seconds
        self._jobs: dict[str, _ProvisionJob] = {}
        self._jobs_lock = threading.Lock()

    def _job_key(self, *, experiment_id: str, sandbox_uid: str = "") -> str:
        return sandbox_uid or experiment_id

    def _provider_for(self, *, req: SandboxRequest) -> str:
        try:
            return self.backend.capabilities_for(provider=req.provider).name
        except Exception:  # noqa: BLE001 — row bookkeeping must not fail a provision
            return req.provider or ""

    def _job_for_row(
        self, *, experiment_id: str, sandbox_uid: str = ""
    ) -> _ProvisionJob | None:
        # Default provisioning jobs predate the row uid; additional jobs use it.
        return self._jobs.get(sandbox_uid) or self._jobs.get(experiment_id)

    def job_is_live(self, *, experiment_id: str, sandbox_uid: str = "") -> bool:
        """A live local job owns its row at any age, including long cold boots."""
        with self._jobs_lock:
            job = self._job_for_row(
                experiment_id=experiment_id, sandbox_uid=sandbox_uid
            )
            return bool(job and job.thread.is_alive())

    # ---------- jobs ----------

    def ensure_job(
        self,
        *,
        experiment_id: str,
        project_id: str,
        req: SandboxRequest,
        sandbox_uid: str = "",
    ) -> _ProvisionJob:
        """Reuse the UID's in-flight job or start one after durable reservation."""
        sandbox_uid = str(sandbox_uid or req.sandbox_uid or "").strip()
        if not sandbox_uid:
            sandbox_uid = self.repository.new_sandbox_uid()
        with self._jobs_lock:
            job = self._jobs.get(sandbox_uid)
            if job is not None and job.thread.is_alive():
                return job
        with self._jobs_lock:
            job = self._jobs.get(sandbox_uid)
            if job is not None and job.thread.is_alive():
                return job
            cancel = threading.Event()
            done = threading.Event()
            thread = threading.Thread(
                target=self._provision,
                args=(experiment_id, project_id, req, cancel, done, sandbox_uid),
                name=f"provision-{sandbox_uid or experiment_id}",
                daemon=True,
            )
            job = _ProvisionJob(
                thread=thread,
                cancel=cancel,
                done=done,
                experiment_id=experiment_id,
                sandbox_uid=sandbox_uid,
            )
            self._jobs[sandbox_uid] = job
            thread.start()
            return job

    def cancel(self, *, experiment_id: str, sandbox_uid: str | None = None) -> None:
        target_uid = (sandbox_uid or "").strip()
        with self._jobs_lock:
            jobs = [
                job
                for job in self._jobs.values()
                if job.experiment_id == experiment_id
                and (not target_uid or job.sandbox_uid == target_uid)
            ]
        for job in jobs:
            job.cancel.set()

    def shutdown(self) -> None:
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel.set()
        for job in jobs:
            with suppress(RuntimeError):
                job.thread.join(timeout=2.0)

    def _provision(
        self,
        experiment_id: str,
        project_id: str,
        req: SandboxRequest,
        cancel: threading.Event,
        done: threading.Event,
        sandbox_uid: str = "",
    ) -> None:
        try:

            def on_phase(phase: str, detail: str) -> None:
                if cancel.is_set():
                    raise _Canceled()
                self.set_provision(
                    experiment_id=experiment_id,
                    sandbox_uid=sandbox_uid,
                    project_id=project_id,
                    phase=phase,
                    detail=detail,
                )

            def on_created(sandbox_id: str, sandbox_name: str) -> None:
                # Persist immediately; a crash must not erase the cleanup handle.
                self.set_provision(
                    experiment_id=experiment_id,
                    sandbox_uid=sandbox_uid,
                    project_id=project_id,
                    sandbox_id=sandbox_id,
                    sandbox_name=sandbox_name,
                )
                if cancel.is_set():
                    raise _Canceled()

            provisioned = self.backend.acquire(
                request=req, on_phase=on_phase, on_created=on_created
            )
            # The final tunnel wait is uninterruptible; recheck before publish.
            if cancel.is_set():
                self.lifecycle.terminate_quietly(sandbox_id=provisioned.sandbox_id)
                self._settle_canceled(
                    experiment_id=experiment_id,
                    project_id=project_id,
                    sandbox_uid=sandbox_uid,
                )
                return
            now = now_iso()
            provider = self._provider_for(req=req)
            instance_type = provisioned.instance_type or (req.instance_type or "")
            gpu = provisioned.gpu or (req.gpu or "")
            generation_id = self.repository.complete_provision(
                experiment_id=experiment_id,
                sandbox_uid=sandbox_uid,
                project_id=project_id,
                fields={
                    "status": "running",
                    "sandbox_id": provisioned.sandbox_id,
                    "provider": provider,
                    "gpu": gpu,
                    "cpu": (
                        provisioned.cpu if provisioned.cpu is not None else req.cpu
                    ),
                    "memory": (
                        provisioned.memory
                        if provisioned.memory is not None
                        else int(req.memory)
                    ),
                    "instance_type": instance_type,
                    "region": provisioned.region or (req.region or ""),
                    "price_usd_per_hour": provisioned.price_usd_per_hour,
                    "ssh_host": provisioned.ssh_host,
                    "ssh_port": provisioned.ssh_port,
                    "ssh_user": provisioned.ssh_user,
                    "workdir": provisioned.workdir,
                    "sync_dir": provisioned.sync_dir or provisioned.workdir,
                    "unsynced_dir": (
                        provisioned.unsynced_dir or provisioned.sandbox_data_dir
                    ),
                    "sandbox_data_dir": provisioned.sandbox_data_dir,
                    "volume_name": provisioned.volume_name,
                    "expires_at": iso_after(seconds=req.time_limit),
                    "last_seen_at": now,
                    "phase": "",
                    "detail": "",
                    "error": "",
                    "terminated_at": "",
                },
                generation={
                    "sandbox_id": provisioned.sandbox_id,
                    "provider": provider,
                    "instance_type": instance_type,
                    "gpu": gpu,
                    "price_usd_per_hour": provisioned.price_usd_per_hour,
                    "key_id": req.key_id,
                },
            )
            if generation_id is None:
                # Release/reap won the row; clean only this provider resource.
                self.lifecycle.terminate_quietly(sandbox_id=provisioned.sandbox_id)
                return
            self.repository.emit_event(
                project_id=project_id,
                event_type="sandbox.created",
                experiment_id=experiment_id,
                payload={
                    "sandbox_id": provisioned.sandbox_id,
                    "gpu": provisioned.gpu or req.gpu or "",
                    "instance_type": provisioned.instance_type
                    or (req.instance_type or ""),
                    "region": provisioned.region or (req.region or ""),
                    "time_limit": req.time_limit,
                },
            )
        except _Canceled:
            self._settle_canceled(
                experiment_id=experiment_id,
                project_id=project_id,
                sandbox_uid=sandbox_uid,
            )
        except (
            BackendUnavailableError,
            BackendValidationError,
            BackendPermissionError,
        ) as exc:
            self._settle_failed(
                experiment_id=experiment_id,
                project_id=project_id,
                error=str(exc),
                sandbox_uid=sandbox_uid,
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 — never lose the row to an unexpected error
            self._settle_failed(
                experiment_id=experiment_id,
                project_id=project_id,
                error=str(exc),
                sandbox_uid=sandbox_uid,
            )
        finally:
            done.set()
            job_key = self._job_key(
                experiment_id=experiment_id, sandbox_uid=sandbox_uid
            )
            with self._jobs_lock:
                current = self._jobs.get(job_key)
                if current is not None and current.done is done:
                    self._jobs.pop(job_key, None)

    def set_provision(
        self,
        *,
        experiment_id: str,
        sandbox_uid: str = "",
        project_id: str = "",
        phase: str | None = None,
        detail: str | None = None,
        sandbox_id: str | None = None,
        sandbox_name: str | None = None,
    ) -> None:
        """Publish callback progress only to the job's project-owned row."""
        fields: dict[str, Any] = {}
        if phase is not None:
            fields["phase"] = phase
        if detail is not None:
            fields["detail"] = detail
        if sandbox_id is not None:
            fields["sandbox_id"] = sandbox_id
        if sandbox_name is not None:
            fields["sandbox_name"] = sandbox_name
        if not self.repository.update_provisioning(
            sandbox_uid=sandbox_uid,
            expected_project_id=project_id,
            fields=fields,
        ):
            raise _Canceled()

    def reap_stale_provisions(self, *, now: datetime, deadline_seconds: float) -> int:
        """Clean old provisioning rows whose local job is gone.

        Both age and live-job checks are required: provider resources may exist
        before the native ID callback, while legitimate cold boots can exceed
        the wall-clock deadline.
        """
        reaped = 0
        for row in self.repository.list_rows_by_status(status="provisioning"):
            experiment_id = str(row.get("experiment_id") or "")
            sandbox_uid = str(row.get("sandbox_uid") or "")
            if self.job_is_live(experiment_id=experiment_id, sandbox_uid=sandbox_uid):
                continue
            started = parse_iso(row.get("provision_started_at"))
            if started is None or (now - started).total_seconds() < deadline_seconds:
                continue
            # The job may have settled after the sweep snapshot.
            fresh = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
            if fresh.get("status") != "provisioning":
                continue
            try:
                outcome = self.lifecycle.settle(
                    row=fresh,
                    trigger="stale_provision",
                    event_type="sandbox.failed",
                    payload={
                        "error": "stale provision reaped",
                        "phase": fresh.get("phase", ""),
                        "sandbox_id": fresh.get("sandbox_id", ""),
                    },
                    error=(
                        "provisioning wedged past deadline (daemon offline?); "
                        "the sandbox was terminated — call sandbox.request again"
                    ),
                )
                if outcome != "maybe_alive":
                    reaped += 1
            except Exception:  # noqa: BLE001 — one bad row never aborts the pass
                continue
        return reaped

    # ---------- settle helpers ----------

    def _settle_row(
        self, *, experiment_id: str, project_id: str, sandbox_uid: str
    ) -> dict[str, Any]:
        try:
            return self.repository.get_by_uid(sandbox_uid=sandbox_uid)
        except Exception:  # noqa: BLE001 — the row may never have been written
            return {
                "experiment_id": experiment_id,
                "project_id": project_id,
                "sandbox_uid": sandbox_uid,
            }

    def _settle_canceled(
        self, *, experiment_id: str, project_id: str, sandbox_uid: str = ""
    ) -> None:
        # Adapters suppress terminate failures, so lifecycle must confirm absence.
        row = self._settle_row(
            experiment_id=experiment_id,
            project_id=project_id,
            sandbox_uid=sandbox_uid,
        )
        if row.get("status") != "provisioning":
            return
        self.lifecycle.settle(
            row=row,
            trigger="provision_canceled",
            event_type="sandbox.released",
            payload={"canceled": True},
        )

    def _settle_failed(
        self,
        *,
        experiment_id: str,
        project_id: str,
        error: str,
        sandbox_uid: str = "",
    ) -> None:
        row = self._settle_row(
            experiment_id=experiment_id,
            project_id=project_id,
            sandbox_uid=sandbox_uid,
        )
        if row.get("status") != "provisioning":
            return
        self.lifecycle.settle(
            row=row,
            trigger="provision_failed",
            event_type="sandbox.failed",
            payload={"error": error},
            error=error,
        )

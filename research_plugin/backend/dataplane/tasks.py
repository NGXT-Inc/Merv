"""The control→data task channel (cloud plan Phase 4, fixed decision 2).

Every "control plane signals the data plane" flow is a **task**: the control
plane enqueues, the data plane executes and acks. The cloud never dials in —
in split mode (Phase 8) this channel is the daemon's long-poll task loop; in
local mode ``InProcessTaskChannel`` degenerates to a synchronous dispatch the
moment a task is enqueued, preserving today's provision/reap/teardown
ordering exactly.

Task types: ``sync_pull`` | ``final_pull`` | ``conn_refresh`` | ``teardown`` |
``parachute_restore``. The last unpacks a reaped sandbox's
parachute object (plan Phase 5, fixed decision 5) into the experiment's
local folder through the worker's normal sync-path semantics.

Payloads carry live objects (sync sessions, row dicts, progress callbacks)
while both planes share one process. Parachute restore is URL-first so the
control plane never has to load the recovered archive into memory. Deadlines
are cloud-minted ISO instants the data plane treats as opaque (plan §3.2) —
unenforced in-process, where the worker is by definition reachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..utils import ValidationError, new_id

if TYPE_CHECKING:
    # Typing-only: a runtime import would load the local worker stack
    # (workspace, rsync, dashboard tunnels) and break import-time separation
    # for `backend.dataplane` as an entry point.
    from .worker import DataPlaneWorker


TASK_TYPES: frozenset[str] = frozenset(
    {
        "sync_pull",
        "final_pull",
        "conn_refresh",
        "teardown",
        "parachute_restore",
    }
)


@dataclass(frozen=True)
class Task:
    """One unit of data-plane work, minted by the control plane."""

    id: str
    type: str
    payload: dict[str, Any]
    # Cloud-authoritative ISO instant; opaque to the data plane.
    deadline: str | None = None


class InProcessTaskChannel:
    """Local-mode channel: enqueue == execute == ack, in submission order.

    Dispatches to the worker synchronously the moment a task is submitted, so
    callers observe exactly the ordering they had before the channel existed.
    Every task and its ack are recorded in memory — the observation seam the
    tests (and, later, the split-mode ack protocol) rely on. A failing task
    re-raises to the submitter after recording the failed ack, preserving the
    callers' existing error handling.
    """

    def __init__(self, *, worker: DataPlaneWorker) -> None:
        self.worker = worker
        # (task, ack) pairs in dispatch order.
        self.history: list[tuple[Task, dict[str, Any]]] = []

    def submit(
        self,
        *,
        task_type: str,
        payload: dict[str, Any],
        deadline: str | None = None,
        tenant_id: str | None = None,  # noqa: ARG002 - HTTP channel uses this
    ) -> Any:
        if task_type not in TASK_TYPES:
            raise ValidationError(f"unknown task type: {task_type}")
        task = Task(
            id=new_id(prefix="task"),
            type=task_type,
            payload=dict(payload),
            deadline=deadline,
        )
        try:
            result = self._execute(task=task)
        except BaseException as exc:
            self.history.append(
                (task, {"task_id": task.id, "ok": False, "error": str(exc)})
            )
            raise
        self.history.append((task, {"task_id": task.id, "ok": True}))
        return result

    def _execute(self, *, task: Task) -> Any:
        payload = task.payload
        if task.type == "final_pull":
            result = self.worker.final_pull(
                session=payload["session"],
                name=str(payload.get("name") or ""),
                deadline=task.deadline,
            )
            return self._with_metrics_snapshot(result=result, payload=payload)
        if task.type == "sync_pull":
            result = self.worker.sync_pull(
                session=payload["session"],
                name=str(payload.get("name") or ""),
                skip_if_busy=bool(payload.get("skip_if_busy")),
            )
            return self._with_metrics_snapshot(result=result, payload=payload)
        if task.type == "conn_refresh":
            # Re-render the agent's conn file (and ssh command) for a row
            # whose tunnel endpoint moved.
            return self.worker.sandbox_enrichment(
                row=payload["row"],
                name=str(payload.get("name") or ""),
                use_sandbox_uid_command=bool(payload.get("use_sandbox_uid_command")),
            )
        if task.type == "teardown":
            # sandbox_id is None when the row itself was missing: skip tunnel
            # teardown but still drop the conn file (pre-channel behavior).
            sandbox_id = payload.get("sandbox_id")
            if sandbox_id is not None:
                self.worker.stop_dashboards(sandbox_id=str(sandbox_id))
                self.worker.stop_mlflow_access(sandbox_id=str(sandbox_id))
            self.worker.remove_conn_file(
                experiment_id=str(payload["experiment_id"]),
                sandbox_uid=str(payload.get("sandbox_uid") or ""),
                remove_experiment_alias=bool(
                    payload.get("remove_experiment_alias", True)
                ),
            )
            return None
        # parachute_restore: unpack a reaped sandbox's parachute object into
        # the experiment's local folder (plan Phase 5). Prefer a read URL so
        # control does not load the archive before dispatch; inline bytes are
        # still accepted for old in-process tests and callers.
        data = payload.get("data")
        if data is None:
            url = str(payload.get("get_url") or "")
            if not url:
                raise ValidationError("parachute_restore task has no get_url")
            from urllib.request import urlopen

            with urlopen(url, timeout=120) as response:  # noqa: S310
                data = response.read()
        return self.worker.restore_parachute(
            experiment_id=str(payload["experiment_id"]),
            data=data,
            name=str(payload.get("name") or ""),
        )

    def _with_metrics_snapshot(self, *, result: Any, payload: dict[str, Any]) -> Any:
        if not isinstance(result, dict):
            return result
        result = dict(result)
        result["metrics_snapshot"] = None
        if result.get("skipped"):
            return result
        row = payload.get("row")
        if not isinstance(row, dict):
            return result
        try:
            result["metrics_snapshot"] = self.worker.capture_metrics_snapshot(
                row=row,
                name=str(payload.get("name") or ""),
            )
        except Exception:  # noqa: BLE001 — metrics capture must not fail sync
            result["metrics_snapshot"] = None
        return result

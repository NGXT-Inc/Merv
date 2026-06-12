"""The control→data task channel (cloud plan Phase 4, fixed decision 2).

Every "control plane signals the data plane" flow is a **task**: the control
plane enqueues, the data plane executes and acks. The cloud never dials in —
in split mode (Phase 8) this channel is the daemon's long-poll task loop; in
local mode ``InProcessTaskChannel`` degenerates to a synchronous dispatch the
moment a task is enqueued, preserving today's provision/reap/teardown
ordering exactly.

Task types: ``initial_push`` | ``final_pull`` | ``conn_refresh`` |
``teardown`` | ``parachute_restore``. The last is stub-acked until plan
Phase 5 supplies the machinery it needs (management keys, the presigned
parachute upload and its recorded object).

Payloads carry live objects (sync sessions, row dicts, progress callbacks)
while both planes share one process; Phase 8 serializes them. Deadlines are
cloud-minted ISO instants the data plane treats as opaque (plan §3.2) —
unenforced in-process, where the worker is by definition reachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..utils import ValidationError, new_id
from .worker import DataPlaneWorker


TASK_TYPES: frozenset[str] = frozenset(
    {"initial_push", "final_pull", "conn_refresh", "teardown", "parachute_restore"}
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
        if task.type == "initial_push":
            return self.worker.push_initial(
                session=payload["session"],
                name=str(payload.get("name") or ""),
                on_retry=payload.get("on_retry"),
            )
        if task.type == "final_pull":
            return self.worker.final_pull(
                session=payload["session"],
                name=str(payload.get("name") or ""),
                deadline=task.deadline,
            )
        if task.type == "conn_refresh":
            # Re-render the agent's conn file (and ssh command) for a row
            # whose tunnel endpoint moved.
            return self.worker.sandbox_enrichment(
                row=payload["row"], name=str(payload.get("name") or "")
            )
        if task.type == "teardown":
            # sandbox_id is None when the row itself was missing: skip tunnel
            # teardown but still drop the conn file (pre-channel behavior).
            sandbox_id = payload.get("sandbox_id")
            if sandbox_id is not None:
                self.worker.stop_dashboards(sandbox_id=str(sandbox_id))
            self.worker.remove_conn_file(
                experiment_id=str(payload["experiment_id"])
            )
            return None
        # parachute_restore: restoring a reaped sandbox's parachute object
        # needs plan Phase 5's machinery (management keys, presigned upload,
        # the recorded {object_key, sha256, ...}); stub-acked until then.
        return {"unsupported": "parachute_restore arrives with plan Phase 5"}

"""Data-plane worker: every local-IO duty behind one interface.

Phase 3 of docs/CLOUD_BACKEND_MIGRATION_PLAN.md carves the control/data seam
in-process: control-plane code (records, gates, lifecycle) never touches the
local filesystem or local processes directly — it calls a ``DataPlaneWorker``.
The local-mode implementation wraps today's machinery (conn files, rsync,
dashboard tunnels). Phase 4 adds the task channel — control enqueues, data
executes — which Phase 8 turns into the daemon's long-poll task loop.
"""

from .state import SandboxLocalState
from .tasks import InProcessTaskChannel, Task
from .worker import DataPlaneWorker, LocalDataPlaneWorker

__all__ = [
    "DataPlaneWorker",
    "InProcessTaskChannel",
    "LocalDataPlaneWorker",
    "SandboxLocalState",
    "Task",
]

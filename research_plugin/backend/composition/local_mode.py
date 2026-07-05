"""Compatibility wrapper for the localhost brain preset."""

from __future__ import annotations

from pathlib import Path

from ..control.control_app import ControlApp
from .control_mode import build_control_app


def build_local_app(*, repo_root: Path, db_path: Path) -> ControlApp:
    """Return the unified brain app using local SQLite/dir defaults.

    ``repo_root`` is accepted for old callers but is not used for repo IO.
    ``db_path`` determines the local brain state root.
    """
    del repo_root
    app, _task_channel = build_control_app(
        repo_root=db_path.parent.parent,
        local_deployment=True,
    )
    return app

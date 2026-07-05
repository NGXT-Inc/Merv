"""Legacy marker helpers for compatibility HTTP harnesses.

The MCP proxy no longer reads ``.research_plugin/daemon.json``; it dials
``RESEARCH_PLUGIN_CONTROL_URL``. These helpers remain only for older tests and
compatibility harnesses that still write a best-effort marker beside a routed
local server.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import now_iso


MARKER_FILENAME = "daemon.json"


def marker_path(*, repo_root: Path) -> Path:
    return repo_root / ".research_plugin" / MARKER_FILENAME


@dataclass(frozen=True)
class DaemonInfo:
    host: str
    port: int
    pid: int
    started_at: str
    repo_root: str

    @property
    def url(self) -> str:
        host = self.host
        # Wrap IPv6 literals so urllib parses them correctly.
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "pid": self.pid,
            "started_at": self.started_at,
            "repo_root": self.repo_root,
        }


def write_marker(*, repo_root: Path, host: str, port: int, pid: int | None = None) -> Path:
    """Write the legacy marker. Best-effort: returns the path even if write fails."""
    info = DaemonInfo(
        host=host,
        port=int(port),
        pid=int(pid if pid is not None else os.getpid()),
        started_at=now_iso(),
        repo_root=str(repo_root),
    )
    path = marker_path(repo_root=repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(info.to_dict(), sort_keys=True), encoding="utf-8")
    except OSError:
        # Don't fail server startup over an unwritable compatibility marker.
        pass
    return path


def clear_marker(*, repo_root: Path) -> None:
    """Remove the legacy marker. Idempotent; ignores missing/permission errors."""
    path = marker_path(repo_root=repo_root)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass

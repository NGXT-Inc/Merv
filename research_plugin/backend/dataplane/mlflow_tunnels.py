"""Reverse SSH tunnels for local managed MLflow access from remote sandboxes."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ..sandbox.sandbox_support import ACTIVE_SANDBOX_STATUSES


@dataclass
class _ReverseTunnel:
    process: subprocess.Popen[Any]
    port: int


class MlflowReverseTunnels:
    """Owns ssh -R processes that expose local MLflow inside remote sandboxes."""

    def __init__(
        self,
        *,
        key_path: Callable[..., Path],
        emit_event: Callable[..., None] | None = None,
    ) -> None:
        self._key_path = key_path
        self.emit_event = emit_event
        self._tunnels: dict[tuple[str, int], _ReverseTunnel] = {}
        self._attempts: dict[tuple[str, int], float] = {}
        self._lock = threading.Lock()

    def ensure(self, *, row: dict[str, Any], tracking_uri: str) -> dict[str, Any]:
        port = _loopback_port(tracking_uri)
        if port is None:
            return {"required": False, "ready": True}
        if row.get("status") not in ACTIVE_SANDBOX_STATUSES:
            return {"required": True, "ready": False, "note": "sandbox is not running"}
        sandbox_id = str(row.get("sandbox_id") or "")
        experiment_id = str(row.get("experiment_id") or "")
        ssh_host = str(row.get("ssh_host") or "")
        if not sandbox_id or not experiment_id or not ssh_host:
            return {"required": True, "ready": False, "note": "sandbox SSH endpoint is unavailable"}
        key = (sandbox_id, port)
        with self._lock:
            tunnel = self._live_tunnel(key=key)
            if tunnel is not None:
                return {"required": True, "ready": True, "tracking_uri": tracking_uri}
            if time.monotonic() - self._attempts.get(key, 0.0) < 10.0:
                return {"required": True, "ready": False, "note": "MLflow reverse tunnel is starting"}
            self._attempts[key] = time.monotonic()
            tunnel = self._start(
                row=row,
                experiment_id=experiment_id,
                sandbox_id=sandbox_id,
                port=port,
            )
            if tunnel is None:
                return {"required": True, "ready": False, "note": "MLflow reverse tunnel is unavailable"}
            self._tunnels[key] = tunnel
            return {"required": True, "ready": True, "tracking_uri": tracking_uri}

    def stop(self, *, sandbox_id: str = "") -> None:
        with self._lock:
            if sandbox_id:
                items = [
                    (key, tunnel)
                    for key, tunnel in self._tunnels.items()
                    if key[0] == sandbox_id
                ]
            else:
                items = list(self._tunnels.items())
            for key, _ in items:
                self._tunnels.pop(key, None)
                self._attempts.pop(key, None)
            for _, tunnel in items:
                _terminate(tunnel.process)

    def _live_tunnel(self, *, key: tuple[str, int]) -> _ReverseTunnel | None:
        tunnel = self._tunnels.get(key)
        if tunnel is None:
            return None
        if tunnel.process.poll() is None:
            return tunnel
        self._tunnels.pop(key, None)
        _terminate(tunnel.process)
        return None

    def _start(
        self,
        *,
        row: dict[str, Any],
        experiment_id: str,
        sandbox_id: str,
        port: int,
    ) -> _ReverseTunnel | None:
        key_path = str(self._key_path(experiment_id=experiment_id))
        command = [
            "ssh",
            "-N",
            "-i",
            key_path,
            "-p",
            str(int(row.get("ssh_port") or 22)),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ConnectTimeout=5",
            "-R",
            f"127.0.0.1:{port}:127.0.0.1:{port}",
            f"{str(row.get('ssh_user') or 'root')}@{str(row.get('ssh_host') or '')}",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None
        if not _forward_bound(process):
            _terminate(process)
            return None
        if self.emit_event is not None:
            self.emit_event(
                project_id=str(row.get("project_id") or ""),
                event_type="sandbox.mlflow_tunneled",
                experiment_id=experiment_id,
                payload={"sandbox_id": sandbox_id, "remote_port": port},
            )
        return _ReverseTunnel(process=process, port=port)


def _loopback_port(uri: str) -> int | None:
    parsed = urlsplit((uri or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None
    try:
        return parsed.port
    except ValueError:
        return None


def _forward_bound(process: subprocess.Popen[Any], *, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        time.sleep(0.1)
    return process.poll() is None


def _terminate(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2.0)
    except Exception:  # noqa: BLE001
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass

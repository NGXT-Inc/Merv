# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Shared management-SSH operations for VM sandbox backends."""

from __future__ import annotations

import re
import socket
import time
from typing import Any, Callable, Mapping

from ..bootstrap_tools import ML_PYTHON_PACKAGES
from ..vm_ssh import (
    SshInputRunner,
    SshRunner,
    read_runs_via_mgmt_ssh,
    read_transcript_via_mgmt_ssh,
    run_ssh,
    run_ssh_input,
    sample_metrics_via_mgmt_ssh,
    sandbox_tokens,
    write_secrets_via_mgmt_ssh,
)
from ..vm_bootstrap import build_standard_user_data
from ...sandbox_backend import (
    BackendUnavailableError,
    SandboxBackendBase,
    SandboxRequest,
    TranscriptTail,
)
from ...sandbox_paths import remote_experiment_dir, remote_root_of, remote_sessions_dir


def _vm_name(experiment_id: str, *, max_length: int = 60) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", experiment_id.lower()).strip("-")
    return f"rp-{safe or 'exp'}"[:max_length]


class VmSshSandboxBackend(SandboxBackendBase):
    """Common management-channel behavior for provisioned VM backends."""

    def __init__(
        self,
        *,
        ssh_runner: SshRunner | None = None,
        ssh_input_runner: SshInputRunner | None = None,
    ) -> None:
        self._ssh_runner = ssh_runner or run_ssh
        self._ssh_input_runner = ssh_input_runner or run_ssh_input

    def _lazy_provider_config(self, factory: Callable[[], Any]) -> Any:
        if self._config is None:
            self._config = factory()
        return self._config

    def _lazy_provider_client(self, factory: Callable[..., Any]) -> Any:
        if self._client is None:
            self._client = factory(config=self.config.cloud)
        return self._client

    def _provisioned_vm_fields(self, *, workdir: str) -> dict[str, Any]:
        return {
            "ssh_user": self.config.ssh_user,
            "workdir": workdir,
            "volume_name": "",
            "sync_dir": workdir,
            "unsynced_dir": self.config.sandbox_data_dir,
            "sandbox_data_dir": self.config.sandbox_data_dir,
            "reused": False,
        }

    def _sandbox_workdir(self, request: SandboxRequest) -> str:
        return request.remote_workdir or remote_experiment_dir(
            experiment_id=request.experiment_id, root=self.config.remote_root
        )

    def _standard_user_data(
        self,
        *,
        request: SandboxRequest,
        workdir: str,
        apt_packages: tuple[str, ...],
    ) -> str:
        return build_standard_user_data(
            public_key=request.public_key,
            experiment_id=request.experiment_id,
            workdir=workdir,
            sessions_dir=remote_sessions_dir(
                experiment_id=request.experiment_id, root=remote_root_of(workdir)
            ),
            sandbox_data_dir=self.config.sandbox_data_dir,
            management_public_key=request.management_public_key,
            apt_packages=apt_packages,
            python_packages=ML_PYTHON_PACKAGES,
        )

    def read_transcript(
        self,
        *,
        sandbox_id: str,
        experiment_id: str,
        volume_name: str,  # noqa: ARG002 — VM backends have no volume
        workdir: str,
        tail: int | None = None,
        ssh_host: str = "",
        ssh_port: int = 0,
        ssh_user: str = "",  # noqa: ARG002 — management uses its own principal, not the caller SSH user
        key_path: str = "",
    ) -> TranscriptTail:
        """Tail exact bytes without recording the management read itself."""
        return read_transcript_via_mgmt_ssh(
            ssh_runner=self._ssh_runner,
            sandbox_id=sandbox_id,
            experiment_id=experiment_id,
            workdir=workdir,
            remote_root=self.config.remote_root,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            key_path=key_path,
            tail=tail,
        )

    def sample_metrics(
        self,
        *,
        sandbox_id: str,
        ssh_host: str = "",
        ssh_port: int = 0,
        ssh_user: str = "",  # noqa: ARG002 — management uses its own principal, not the caller SSH user
        key_path: str = "",
    ) -> dict[str, Any] | None:
        """Sample gauges without recording management polling."""
        return sample_metrics_via_mgmt_ssh(
            ssh_runner=self._ssh_runner,
            sandbox_id=sandbox_id,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            key_path=key_path,
        )

    def read_runs(
        self,
        *,
        sandbox_id: str,
        workdir: str,
        ssh_host: str = "",
        ssh_port: int = 0,
        ssh_user: str = "",  # noqa: ARG002 — management uses its own principal, not the caller SSH user
        key_path: str = "",
    ) -> list[dict[str, Any]] | None:
        """Return receipts; ``[]`` is empty and ``None`` means no news."""
        return read_runs_via_mgmt_ssh(
            ssh_runner=self._ssh_runner,
            sandbox_id=sandbox_id,
            workdir=workdir,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            key_path=key_path,
        )

    def sandbox_secrets(self, *, hf_token: str = "") -> dict[str, str]:
        return sandbox_tokens(hf_token=hf_token)

    def _wait_for_ssh(self, *, host: str, port: int = 22) -> None:
        deadline = time.monotonic() + self.config.poll_timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=10):
                    return
            except OSError as exc:
                last_error = str(exc)
                time.sleep(self.config.poll_interval_seconds)
        raise BackendUnavailableError(
            f"SSH never became reachable on {host}:{port} ({last_error})"
        )

    def write_secrets(
        self,
        *,
        sandbox_id: str,
        secrets: Mapping[str, str],
        ssh_host: str = "",
        ssh_port: int = 0,
        key_path: str = "",
    ) -> bool:
        """Deliver secrets after boot; failure cannot fail provisioning."""
        return write_secrets_via_mgmt_ssh(
            ssh_runner=self._ssh_input_runner,
            sandbox_id=sandbox_id,
            secrets=secrets,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            key_path=key_path,
        )


__all__ = [
    "SshInputRunner",
    "SshRunner",
    "VmSshSandboxBackend",
]

"""Lambda Labs VM sandbox backend.

This backend provisions a Lambda Cloud VM and returns SSH details to the agent.
It intentionally does not implement repo/filesystem sync yet; the VM bootstrap
only prepares a normal developer shell with the tools agents expect.
"""

from __future__ import annotations

import base64
import re
import shlex
import socket
import time
from pathlib import Path
from typing import Any

from backend.execution.bootstrap_tools import (
    LAMBDA_APT_PACKAGES,
    ML_PYTHON_PACKAGES,
)
from ...errors import BackendUnavailableError, BackendValidationError
from ...types import (
    BackendCapabilities,
    OnCreated,
    OnPhase,
    ProvisionedSandbox,
    SandboxRequest,
)
from .client import LambdaCloudClient
from .config import LambdaSandboxConfig


SESSIONS_DIR_NAME = ".research_plugin_sessions"
TRANSCRIPT_FILENAME = "transcript.log"
ACTIVE_INSTANCE_STATUSES = frozenset({"active"})
LIVE_INSTANCE_STATUSES = frozenset({"booting", "active", "unhealthy"})


REC_SCRIPT = r"""#!/usr/bin/env bash
[ -f /opt/rp/env ] && . /opt/rp/env
RP_WORKDIR="${RP_WORKDIR:-/workspace/repo}"
RP_EXPERIMENT_ID="${RP_EXPERIMENT_ID:-unknown}"
RP_SANDBOX_DATA_DIR="${RP_SANDBOX_DATA_DIR:-/workspace/sandbox_data}"
RP_DATASET_DIR="${RP_DATASET_DIR:-$RP_SANDBOX_DATA_DIR}"
RP_TB_LOGDIR="${RP_TB_LOGDIR:-$RP_WORKDIR/.research_plugin_sessions/$RP_EXPERIMENT_ID/tb}"
MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI:-http://localhost:5000}"
export RP_WORKDIR RP_EXPERIMENT_ID RP_SANDBOX_DATA_DIR RP_DATASET_DIR RP_TB_LOGDIR MLFLOW_TRACKING_URI
mkdir -p "$RP_WORKDIR" "$RP_SANDBOX_DATA_DIR" 2>/dev/null || true
LOG_DIR="$RP_WORKDIR/.research_plugin_sessions/$RP_EXPERIMENT_ID"
LOG="$LOG_DIR/transcript.log"
mkdir -p "$LOG_DIR" 2>/dev/null || true
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  { printf '\n[%s] $ %s\n' "$(ts)" "$SSH_ORIGINAL_COMMAND" >> "$LOG"; } 2>/dev/null || true
  cd "$RP_WORKDIR" 2>/dev/null || true
  bash -lc "$SSH_ORIGINAL_COMMAND" 2>&1 | tee -a "$LOG"
  rc=${PIPESTATUS[0]}
  { printf '[%s] (exit %d)\n' "$(ts)" "$rc" >> "$LOG"; } 2>/dev/null || true
  exit "$rc"
else
  { printf '\n[%s] (interactive shell)\n' "$(ts)" >> "$LOG"; } 2>/dev/null || true
  cd "$RP_WORKDIR" 2>/dev/null || true
  exec bash -l
fi
"""


class LambdaLabsSandboxBackend:
    capabilities = BackendCapabilities(name="lambda_labs")

    def __init__(
        self,
        *,
        config: LambdaSandboxConfig | None = None,
        client: LambdaCloudClient | None = None,
    ) -> None:
        self.config = config or LambdaSandboxConfig.from_env()
        self.client = client or LambdaCloudClient(config=self.config.cloud)

    def acquire(
        self,
        *,
        request: SandboxRequest,
        on_phase: OnPhase | None = None,
        on_created: OnCreated | None = None,
    ) -> ProvisionedSandbox:
        instance_name = _sandbox_name(request.experiment_id)
        key_name = f"{instance_name}-key"
        _call(on_phase, "checking_capacity", self.config.instance_type_name)
        self._ensure_capacity(requested_gpu=request.gpu)

        _call(on_phase, "registering_ssh_key", key_name)
        key_id = ""
        instance_id = ""
        try:
            key = self.client.add_ssh_key(name=key_name, public_key=request.public_key)
            key_id = str(key.get("id") or "")

            _call(on_phase, "creating", f"{self.config.instance_type_name} in {self.config.region_name}")
            user_data = build_user_data(
                public_key=request.public_key,
                experiment_id=request.experiment_id,
                workdir=request.remote_workdir or self.config.remote_workdir,
                sandbox_data_dir=self.config.sandbox_data_dir,
            )
            instance_id = self.client.launch_instance(
                region_name=self.config.region_name,
                instance_type_name=self.config.instance_type_name,
                ssh_key_name=key_name,
                name=instance_name,
                user_data=user_data,
            )
            _call(on_created, instance_id, instance_name)

            _call(on_phase, "connecting", "waiting for active instance and ssh")
            instance = self._wait_for_active_instance(instance_id=instance_id)
            ip = str(instance.get("ip") or instance.get("hostname") or "")
            if not ip:
                raise BackendUnavailableError("Lambda instance became active without a public IP")
            self._wait_for_ssh(host=ip)
            return ProvisionedSandbox(
                sandbox_id=instance_id,
                ssh_host=ip,
                ssh_port=22,
                ssh_user=self.config.ssh_user,
                workdir=request.remote_workdir or self.config.remote_workdir,
                volume_name="",
                sandbox_data_dir=self.config.sandbox_data_dir,
                reused=False,
                dashboards={},
            )
        except Exception:
            if instance_id:
                try:
                    self.client.terminate_instances([instance_id])
                except Exception:  # noqa: BLE001
                    pass
            if key_id:
                try:
                    self.client.delete_ssh_key(key_id)
                except Exception:  # noqa: BLE001
                    pass
            raise

    def is_alive(self, *, sandbox_id: str) -> bool:
        if not sandbox_id:
            return False
        try:
            instance = self.client.get_instance(sandbox_id)
        except Exception:  # noqa: BLE001
            return False
        return str(instance.get("status") or "") in LIVE_INSTANCE_STATUSES

    def terminate(self, *, sandbox_id: str) -> bool:
        if not sandbox_id:
            return False
        key_names = self._ssh_key_names_for_instance(sandbox_id=sandbox_id)
        try:
            self.client.terminate_instances([sandbox_id])
        except Exception:  # noqa: BLE001
            return False
        self._delete_ssh_keys_by_name(key_names)
        return True

    def read_transcript(
        self,
        *,
        sandbox_id: str,
        experiment_id: str,
        volume_name: str,
        workdir: str,
        tail: int | None = None,
    ) -> str:
        return (
            "Lambda transcript retrieval over SSH is not implemented yet. "
            "The VM records commands at "
            f"{workdir}/{SESSIONS_DIR_NAME}/{experiment_id}/{TRANSCRIPT_FILENAME}."
        )

    def sync_sandbox_files(
        self,
        *,
        project_id: str,
        sandbox_id: str,
        workdir: str,
        volume_name: str,
    ) -> dict:
        raise BackendUnavailableError("Lambda sandbox file sync is not implemented yet")

    def sandbox_environment(self) -> dict:
        return {
            "backend": "lambda_labs",
            "region": self.config.region_name,
            "instance_type": self.config.instance_type_name,
            "workdir": self.config.remote_workdir,
            "sandbox_data_dir": self.config.sandbox_data_dir,
            "sync": "not_supported",
        }

    def health(self) -> dict:
        try:
            self.client.list_instance_types()
            return {"ok": True, "backend": "lambda_labs"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "backend": "lambda_labs", "error": str(exc)}

    def find_sandbox_id(self, *, experiment_id: str) -> str | None:
        name = _sandbox_name(experiment_id)
        try:
            for instance in self.client.list_instances():
                if instance.get("name") == name and str(instance.get("status") or "") in LIVE_INSTANCE_STATUSES:
                    return str(instance.get("id") or "") or None
        except Exception:  # noqa: BLE001
            return None
        return None

    def _ensure_capacity(self, *, requested_gpu: str | None) -> None:
        instance_types = self.client.list_instance_types()
        row = instance_types.get(self.config.instance_type_name)
        if not isinstance(row, dict):
            raise BackendValidationError(
                f"Lambda instance type is not currently offered: {self.config.instance_type_name}"
            )
        instance_type = row.get("instance_type")
        if not isinstance(instance_type, dict):
            raise BackendUnavailableError("Lambda Cloud returned malformed instance type data")
        if requested_gpu:
            gpu_text = " ".join(
                str(instance_type.get(key) or "")
                for key in ("name", "description", "gpu_description")
            ).upper()
            if requested_gpu.upper() not in gpu_text:
                raise BackendValidationError(
                    f"requested gpu {requested_gpu} does not match configured Lambda "
                    f"instance type {self.config.instance_type_name}"
                )
        regions = row.get("regions_with_capacity_available")
        if not isinstance(regions, list):
            raise BackendUnavailableError("Lambda Cloud returned malformed capacity data")
        available_regions = {
            str(region.get("name") or "")
            for region in regions
            if isinstance(region, dict)
        }
        if self.config.region_name not in available_regions:
            raise BackendUnavailableError(
                f"Lambda instance type {self.config.instance_type_name} has no current "
                f"capacity in {self.config.region_name}"
            )

    def _wait_for_active_instance(self, *, instance_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.poll_timeout_seconds
        last_status = ""
        while time.monotonic() < deadline:
            instance = self.client.get_instance(instance_id)
            last_status = str(instance.get("status") or "")
            if last_status in ACTIVE_INSTANCE_STATUSES and (instance.get("ip") or instance.get("hostname")):
                return instance
            if last_status in {"terminated", "terminating", "preempted"}:
                raise BackendUnavailableError(
                    f"Lambda instance {instance_id} reached terminal status {last_status}"
                )
            time.sleep(self.config.poll_interval_seconds)
        raise BackendUnavailableError(
            f"Lambda instance {instance_id} did not become active before timeout "
            f"(last status: {last_status or 'unknown'})"
        )

    def _wait_for_ssh(self, *, host: str) -> None:
        deadline = time.monotonic() + self.config.poll_timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, 22), timeout=10):
                    return
            except OSError as exc:
                last_error = str(exc)
                time.sleep(self.config.poll_interval_seconds)
        raise BackendUnavailableError(f"SSH never became reachable on {host}:22 ({last_error})")

    def _ssh_key_names_for_instance(self, *, sandbox_id: str) -> list[str]:
        try:
            instance = self.client.get_instance(sandbox_id)
        except Exception:  # noqa: BLE001
            return []
        names = instance.get("ssh_key_names")
        if not isinstance(names, list):
            return []
        return [str(name) for name in names if str(name).startswith("rp-")]

    def _delete_ssh_keys_by_name(self, names: list[str]) -> None:
        if not names:
            return
        wanted = set(names)
        try:
            keys = self.client.list_ssh_keys()
        except Exception:  # noqa: BLE001
            return
        for key in keys:
            key_name = str(key.get("name") or "")
            key_id = str(key.get("id") or "")
            if key_name in wanted and key_id:
                try:
                    self.client.delete_ssh_key(key_id)
                except Exception:  # noqa: BLE001
                    pass


def build_user_data(
    *,
    public_key: str,
    experiment_id: str,
    workdir: str,
    sandbox_data_dir: str,
) -> str:
    apt_packages = " ".join(shlex.quote(pkg) for pkg in LAMBDA_APT_PACKAGES)
    python_packages = " ".join(shlex.quote(pkg) for pkg in (*ML_PYTHON_PACKAGES, "mlflow==2.18.0", "tensorboard==2.18.0"))
    public_key_b64 = base64.b64encode(public_key.encode("utf-8")).decode("ascii")
    rec_script_b64 = base64.b64encode(REC_SCRIPT.encode("utf-8")).decode("ascii")
    env_lines = "\n".join(
        [
            f"RP_WORKDIR={shlex.quote(workdir)}",
            f"RP_EXPERIMENT_ID={shlex.quote(experiment_id)}",
            f"RP_SANDBOX_DATA_DIR={shlex.quote(sandbox_data_dir)}",
            f"RP_DATASET_DIR={shlex.quote(sandbox_data_dir)}",
            f"RP_TB_LOGDIR={shlex.quote(workdir + '/' + SESSIONS_DIR_NAME + '/' + experiment_id + '/tb')}",
            "MLFLOW_TRACKING_URI=http://localhost:5000",
        ]
    )
    return f"""#!/usr/bin/env bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends {apt_packages}
ln -sf /usr/bin/fdfind /usr/local/bin/fd || true
python3 -m pip install --break-system-packages --upgrade pip uv || python3 -m pip install --user --upgrade pip uv || true
if [ -x /root/.local/bin/uv ]; then
  install -m 0755 /root/.local/bin/uv /usr/local/bin/uv
fi
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh || true
  if [ -x /root/.local/bin/uv ]; then
    install -m 0755 /root/.local/bin/uv /usr/local/bin/uv
  fi
fi
if command -v uv >/dev/null 2>&1; then
  uv pip install --system torch torchvision torchaudio || true
  uv pip install --system {python_packages} || true
else
  python3 -m pip install --break-system-packages torch torchvision torchaudio {python_packages} || true
fi
mkdir -p /opt/rp /root/.ssh {shlex.quote(workdir)} {shlex.quote(sandbox_data_dir)}
printf '%s' {shlex.quote(public_key_b64)} | base64 -d > /root/.ssh/authorized_keys
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
if id ubuntu >/dev/null 2>&1; then
  mkdir -p /home/ubuntu/.ssh
  printf '%s' {shlex.quote(public_key_b64)} | base64 -d >> /home/ubuntu/.ssh/authorized_keys
  chown -R ubuntu:ubuntu /home/ubuntu/.ssh {shlex.quote(workdir)} {shlex.quote(sandbox_data_dir)}
  chmod 700 /home/ubuntu/.ssh
  chmod 600 /home/ubuntu/.ssh/authorized_keys
fi
cat > /opt/rp/env <<'RP_ENV'
{env_lines}
RP_ENV
printf '%s' {shlex.quote(rec_script_b64)} | base64 -d > /opt/rp/rec.sh
chmod +x /opt/rp/rec.sh
cat > /etc/ssh/sshd_config.d/99-research-plugin.conf <<'RP_SSHD'
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
AuthorizedKeysFile .ssh/authorized_keys
ForceCommand /opt/rp/rec.sh
PrintMotd no
AcceptEnv LANG LC_*
RP_SSHD
systemctl restart ssh || systemctl restart sshd || service ssh restart || true
"""


def _sandbox_name(experiment_id: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", experiment_id.lower()).strip("-")
    return f"rp-{safe or 'exp'}"[:60]


def _call(cb: Any, *args: Any) -> None:
    if cb is not None:
        cb(*args)


def build_lambda_labs_sandbox_backend(*, repo_root: Path | None = None, **_kwargs: Any) -> LambdaLabsSandboxBackend:
    return LambdaLabsSandboxBackend(config=LambdaSandboxConfig.from_env())

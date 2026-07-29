# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Modal sandboxes with SSH tunnels and control-plane observation."""

from __future__ import annotations

import base64
from contextlib import suppress
import os
import shlex
import threading
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from ...bootstrap_tools import (
    BASELINE_APT_PACKAGES,
    ML_PYTHON_PACKAGES,
    REC_EXEC_CORE,
)
from ...run_receipts import (
    MERV_RUN_PATH,
    MERV_RUN_SCRIPT,
    parse_runs_listing,
    runs_listing_command,
)
from ...usage_metrics import (
    METRICS_EXEC_TIMEOUT,
    METRICS_SCRIPT,
    parse_metrics,
)
from ....sandbox_backend import BackendUnavailableError
from ....sandbox_paths import remote_experiment_dir, remote_root_of, remote_sessions_dir
from ...transcript_wire import (
    TRANSCRIPT_TAIL_DEFAULT,
    parse_transcript_tail,
    transcript_tail_command,
)


MODAL_APT_PACKAGES: tuple[str, ...] = (
    "openssh-server",
    "ca-certificates",
    *BASELINE_APT_PACKAGES,
)
from ....sandbox_backend import (
    BackendCapabilities,
    OnCreated,
    OnPhase,
    ProvisionedSandbox,
    SandboxBackendBase,
    SandboxRequest,
    TranscriptTail,
)
from .config import COMPUTE_TIERS, DEFAULT_GPU, VALID_GPUS, ModalConfig
from ._sandbox_ops import maybe_await, read_stream, wait_process


ActivityHook = Callable[[str, dict[str, Any]], None]

SESSIONS_DIR_NAME = ".merv_sessions"
TRANSCRIPT_FILENAME = "transcript.log"
# Modal SDK termination has no timeout; bound cleanup-claim occupancy here.
TERMINATE_TIMEOUT_SECONDS = 60.0


def _call_bounded(call: Callable[[], Any], *, timeout: float) -> bool:
    """Bound an uninterruptible SDK call in a disposable daemon thread."""
    done = threading.Event()
    ok: list[bool] = []

    def run() -> None:
        with suppress(Exception):
            maybe_await(call())
            ok.append(True)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return bool(done.wait(timeout=timeout) and ok)

# Image entrypoint configures keys, ForceCommand, and foreground sshd.
BOOT_SCRIPT = r"""#!/usr/bin/env bash
set -eu
RP_EXPERIMENT_ID="${RP_EXPERIMENT_ID:-unknown}"
RP_WORKDIR="${RP_WORKDIR:-/workspace/$RP_EXPERIMENT_ID}"
MERV_EXPERIMENT_DIR="${MERV_EXPERIMENT_DIR:-$RP_WORKDIR}"
RP_SANDBOX_DATA_DIR="${RP_SANDBOX_DATA_DIR:-/workspace/data}"
mkdir -p "$MERV_EXPERIMENT_DIR" "$RP_SANDBOX_DATA_DIR" "$MERV_EXPERIMENT_DIR/artifacts_to_keep"
mkdir -p /root/.ssh && chmod 700 /root/.ssh
# Two keys, two duties: the caller key handles SSH/rsync, while the brain's
# management key handles transcript and metrics operations.
: > /root/.ssh/authorized_keys
if [ -n "${RP_AUTHORIZED_KEY:-}" ]; then
  printf '%s\n' "$RP_AUTHORIZED_KEY" >> /root/.ssh/authorized_keys
fi
if [ -n "${RP_MANAGEMENT_KEY:-}" ]; then
  printf '%s\n' "$RP_MANAGEMENT_KEY" >> /root/.ssh/authorized_keys
fi
chmod 600 /root/.ssh/authorized_keys
# Persist the session env so the ForceCommand wrapper can read it (sshd does not
# pass the container environment through to forced commands).
RP_SESSION_DIR="${RP_SESSION_DIR:-/workspace/.merv_sessions/$RP_EXPERIMENT_ID}"
mkdir -p "$RP_SESSION_DIR" 2>/dev/null || true
{
  printf 'RP_WORKDIR=%q\n' "$MERV_EXPERIMENT_DIR"
  printf 'MERV_EXPERIMENT_DIR=%q\n' "$MERV_EXPERIMENT_DIR"
  printf '# RP_EXPERIMENT_DIR: deprecated one-version twin; remove next release.\n'
  printf 'RP_EXPERIMENT_DIR=%q\n' "$MERV_EXPERIMENT_DIR"
  printf 'RP_EXPERIMENT_ID=%q\n' "$RP_EXPERIMENT_ID"
  printf 'RP_SANDBOX_DATA_DIR=%q\n' "$RP_SANDBOX_DATA_DIR"
  printf 'RP_DATASET_DIR=%q\n' "$RP_SANDBOX_DATA_DIR"
  printf 'RP_SESSION_DIR=%q\n' "$RP_SESSION_DIR"
  if [ -n "${HF_TOKEN:-}" ]; then
    printf 'HF_TOKEN=%q\n' "$HF_TOKEN"
    printf 'HUGGING_FACE_HUB_TOKEN=%q\n' "${HUGGING_FACE_HUB_TOKEN:-$HF_TOKEN}"
  fi
} > /opt/merv/env
mkdir -p /run/sshd
ssh-keygen -A >/dev/null 2>&1 || true
cat > /etc/ssh/sshd_config <<'EOF'
Port 22
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
AuthorizedKeysFile /root/.ssh/authorized_keys
ForceCommand /opt/merv/rec.sh
PrintMotd no
AcceptEnv LANG LC_*
PidFile /run/sshd.pid
EOF
exec /usr/sbin/sshd -D -e
"""


# ForceCommand wrapper: records every SSH channel (interactive shell or
# `ssh host 'cmd'`) to a per-experiment transcript in the sessions dir while
# still streaming output back to the agent. Exit code is preserved.
REC_SCRIPT = r"""#!/usr/bin/env bash
[ -f /opt/merv/env ] && . /opt/merv/env
RP_EXPERIMENT_ID="${RP_EXPERIMENT_ID:-unknown}"
RP_WORKDIR="${RP_WORKDIR:-/workspace/$RP_EXPERIMENT_ID}"
MERV_EXPERIMENT_DIR="${MERV_EXPERIMENT_DIR:-$RP_WORKDIR}"
# RP_EXPERIMENT_DIR: deprecated one-version twin of MERV_EXPERIMENT_DIR; remove next release.
RP_EXPERIMENT_DIR="$MERV_EXPERIMENT_DIR"
RP_SANDBOX_DATA_DIR="${RP_SANDBOX_DATA_DIR:-/workspace/data}"
RP_DATASET_DIR="${RP_DATASET_DIR:-$RP_SANDBOX_DATA_DIR}"
RP_SESSION_DIR="${RP_SESSION_DIR:-/workspace/.merv_sessions/$RP_EXPERIMENT_ID}"
if [ -n "${HF_TOKEN:-}" ] && [ -z "${HUGGING_FACE_HUB_TOKEN:-}" ]; then
  HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"
fi
export RP_WORKDIR MERV_EXPERIMENT_DIR RP_EXPERIMENT_DIR RP_EXPERIMENT_ID RP_SANDBOX_DATA_DIR RP_DATASET_DIR HF_TOKEN HUGGING_FACE_HUB_TOKEN RP_SESSION_DIR
mkdir -p "$MERV_EXPERIMENT_DIR" "$RP_SANDBOX_DATA_DIR" "$MERV_EXPERIMENT_DIR/artifacts_to_keep" "$RP_SESSION_DIR" 2>/dev/null || true
LOG_DIR="$RP_SESSION_DIR"
LOG="$LOG_DIR/transcript.log"
mkdir -p "$LOG_DIR" 2>/dev/null || true
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  # File-transfer protocols (rsync/scp/sftp) speak a binary protocol over stdio
  # and must bypass both the transcript tee and the tmux supervisor (which
  # detaches stdin).
  case "$SSH_ORIGINAL_COMMAND" in
    rsync\ --server*|*"sftp-server"*|internal-sftp*|scp\ -*)
      exec bash -lc "$SSH_ORIGINAL_COMMAND"
      ;;
  esac
  { printf '\n[%s] $ %s\n' "$(ts)" "$SSH_ORIGINAL_COMMAND" >> "$LOG"; } 2>/dev/null || true
  cd "$MERV_EXPERIMENT_DIR" 2>/dev/null || true
""" + REC_EXEC_CORE + r"""
else
  { printf '\n[%s] (interactive shell)\n' "$(ts)" >> "$LOG"; } 2>/dev/null || true
  cd "$MERV_EXPERIMENT_DIR" 2>/dev/null || true
  exec bash -l
fi
"""


class ModalSandboxBackend(SandboxBackendBase):
    capabilities = BackendCapabilities(name="modal")

    def __init__(
        self,
        *,
        repo_root: Path,
        config: ModalConfig | None = None,
        modal_module: Any | None = None,
        activity: ActivityHook | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.config = config or ModalConfig.from_env()
        self.activity = activity
        self._modal = modal_module
        self._app = None
        self._base_image = None
        self._cuda_image = None
        self._lock = threading.Lock()

    # ---------- SandboxBackend protocol ----------

    def acquire(
        self,
        *,
        request: SandboxRequest,
        on_phase: OnPhase | None = None,
        on_created: OnCreated | None = None,
    ) -> ProvisionedSandbox:
        self._ensure_credentials()
        workdir = request.remote_workdir or remote_experiment_dir(
            experiment_id=request.experiment_id, root=self.config.remote_root
        )
        sandbox_data_dir = self.config.sandbox_data_dir
        modal = self._modal_module()
        image = self._image(cuda_devel=request.cuda_devel, image_packages=request.image_packages)
        app = self._get_app()
        env = self._sandbox_env(
            public_key=request.public_key,
            management_public_key=request.management_public_key,
            experiment_id=request.experiment_id,
            workdir=workdir,
            sandbox_data_dir=sandbox_data_dir,
        )
        secrets = self._sandbox_secrets(modal, hf_token=request.hf_token)
        name = _sandbox_name(request.sandbox_uid or request.experiment_id)
        kwargs: dict[str, Any] = {
            "app": app,
            "image": image,
            "timeout": int(request.time_limit),
            "workdir": workdir,
            "unencrypted_ports": [22],
            "env": env,
            "cpu": request.cpu,
            "memory": int(request.memory),
            "name": name,
        }
        if secrets:
            kwargs["secrets"] = secrets
        if request.gpu:
            kwargs["gpu"] = request.gpu
        self._notify(on_phase, "creating", f"gpu={request.gpu or 'cpu'}")
        try:
            sandbox = modal.Sandbox.create("bash", "/opt/merv/boot.sh", **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise BackendUnavailableError(f"Modal sandbox create failed: {exc}") from exc

        sandbox_id = str(getattr(sandbox, "object_id", "") or "")
        # Past this point the sandbox EXISTS on Modal and holds the name. Any
        # failure (tunnel timeout, cancellation via a callback) must terminate it
        # before propagating, or it orphans and the deterministic name collides
        # on every later request.
        try:
            self._set_tags(
                sandbox=sandbox,
                tags={
                    "research_plugin": "true",
                    "research_plugin_role": "sandbox",
                    "experiment_id": request.experiment_id,
                    "project_id": request.project_id,
                },
            )
            # Persist the ID before the slow tunnel wait; the callback may cancel.
            self._notify(on_created, sandbox_id, name or "")
            self._notify(on_phase, "connecting", "waiting for ssh")
            host, port = self._ssh_endpoint(sandbox=sandbox)
        except BaseException:
            with suppress(Exception):
                sandbox.terminate()
            raise
        return ProvisionedSandbox(
            sandbox_id=sandbox_id,
            ssh_host=host,
            ssh_port=port,
            ssh_user="root",
            workdir=workdir,
            volume_name="",
            sync_dir=workdir,
            unsynced_dir=sandbox_data_dir,
            sandbox_data_dir=sandbox_data_dir,
            reused=False,
        )

    def find_sandbox_id(
        self, *, experiment_id: str, sandbox_uid: str = "", provider: str = ""
    ) -> str | None:
        """Find a deterministic-name orphan; only Modal NotFound means absent."""
        name = _sandbox_name(sandbox_uid or experiment_id)
        if not name:
            return None
        try:
            modal = self._modal_module()
            sandbox = modal.Sandbox.from_name(self.config.app_name, name)
            return str(getattr(sandbox, "object_id", "") or "") or None
        except Exception as exc:  # noqa: BLE001
            if "notfound" in type(exc).__name__.lower():
                return None
            raise

    def is_alive(self, *, sandbox_id: str) -> bool:
        if not sandbox_id:
            return False
        try:
            sandbox = self._sandbox_from_id(sandbox_id)
            poll = getattr(sandbox, "poll", None)
            if not callable(poll):
                return True
            return maybe_await(poll()) is None
        except Exception as exc:  # noqa: BLE001
            # modal.exception.NotFoundError = authoritatively gone; anything
            # else (auth, network, SDK) propagates so callers don't mistake an
            # outage for a dead sandbox.
            if "notfound" in type(exc).__name__.lower():
                return False
            raise

    def refresh_ssh_endpoint(self, *, sandbox_id: str) -> tuple[str, int] | None:
        """Best-effort refresh for Modal's movable tunnel endpoint."""
        if not sandbox_id:
            return None
        try:
            sandbox = self._sandbox_from_id(sandbox_id)
            return self._ssh_endpoint(sandbox=sandbox)
        except Exception:  # noqa: BLE001 — caller treats None as "couldn't refresh"
            return None

    def terminate(self, *, sandbox_id: str) -> bool:
        if not sandbox_id:
            return False
        try:
            sandbox = self._sandbox_from_id(sandbox_id)
        except Exception:  # noqa: BLE001
            return False
        # Timeout remains unconfirmed so lifecycle parks and retries the row.
        ok = _call_bounded(sandbox.terminate, timeout=TERMINATE_TIMEOUT_SECONDS)
        detach = getattr(sandbox, "detach", None)
        if callable(detach):
            _call_bounded(detach, timeout=TERMINATE_TIMEOUT_SECONDS)
        return ok

    def read_transcript(
        self,
        *,
        sandbox_id: str,
        experiment_id: str,
        volume_name: str,
        workdir: str,
        tail: int | None = None,
        # SSH connection details are unused: Modal reads via control-plane exec.
        ssh_host: str = "",  # noqa: ARG002
        ssh_port: int = 0,  # noqa: ARG002
        ssh_user: str = "",  # noqa: ARG002
        key_path: str = "",  # noqa: ARG002
    ) -> TranscriptTail:
        limit = int(tail) if tail and tail > 0 else TRANSCRIPT_TAIL_DEFAULT
        live = self._read_transcript_live(
            sandbox_id=sandbox_id,
            experiment_id=experiment_id,
            workdir=workdir,
            limit=limit,
        )
        return live

    def sample_metrics(
        self,
        *,
        sandbox_id: str,
        # SSH connection details are unused: Modal samples via control-plane exec.
        ssh_host: str = "",  # noqa: ARG002
        ssh_port: int = 0,  # noqa: ARG002
        ssh_user: str = "",  # noqa: ARG002
        key_path: str = "",  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Sample usage via read-only control-plane exec."""
        if not sandbox_id:
            return None
        try:
            sandbox = self._sandbox_from_id(sandbox_id)
            process = sandbox.exec("bash", "-c", METRICS_SCRIPT, timeout=METRICS_EXEC_TIMEOUT)
            if wait_process(process) != 0:
                return None
            output = read_stream(getattr(process, "stdout", None))
        except Exception:  # noqa: BLE001
            return None
        return parse_metrics(output)

    def read_runs(
        self,
        *,
        sandbox_id: str,
        workdir: str,
        # SSH connection details are unused: Modal lists via control-plane exec.
        ssh_host: str = "",  # noqa: ARG002
        ssh_port: int = 0,  # noqa: ARG002
        ssh_user: str = "",  # noqa: ARG002
        key_path: str = "",  # noqa: ARG002
    ) -> list[dict[str, Any]] | None:
        """List receipts via exec; ``None`` means no authoritative news."""
        if not sandbox_id or not workdir:
            return None
        try:
            sandbox = self._sandbox_from_id(sandbox_id)
            process = sandbox.exec(
                "bash",
                "-c",
                runs_listing_command(experiment_dir=workdir),
                timeout=METRICS_EXEC_TIMEOUT,
            )
            if wait_process(process) != 0:
                return None
            output = read_stream(getattr(process, "stdout", None))
        except Exception:  # noqa: BLE001
            return None
        return parse_runs_listing(output)

    def hardware_catalog(
        self, *, gpu: str | None = None, region: str | None = None
    ) -> dict[str, Any]:
        """Static menu because Modal composes GPU, CPU, and memory independently."""
        gpus = sorted(VALID_GPUS)
        if gpu:
            needle = gpu.strip().upper()
            gpus = [g for g in gpus if needle in g] or gpus
        return {
            "provider": "modal",
            "selection_required": False,
            "select_with": "gpu+cpu+memory",
            "reason": (
                "Modal composes the machine from the request: choose a gpu type "
                "(or omit for CPU-only) and set cpu cores / memory MiB directly."
            ),
            "gpus": gpus,
            "default_gpu": DEFAULT_GPU,
            "compute_tiers": COMPUTE_TIERS,
            "defaults": {"cpu": 2, "memory_mib": 8192},
            "notes": [
                "Omit gpu for a CPU-only sandbox.",
                "cpu is Modal CPU cores (1 core = 2 vCPUs).",
                "memory is requested sandbox memory in MiB.",
            ],
        }

    def health(self) -> dict[str, Any]:
        try:
            self._ensure_credentials()
            self._get_app()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "name": "modal", "error": str(exc)}
        return {"ok": True, "name": "modal", "app": self.config.app_name}

    # ---------- transcript helpers ----------

    def _read_transcript_live(
        self, *, sandbox_id: str, experiment_id: str, workdir: str, limit: int
    ) -> TranscriptTail:
        if not sandbox_id:
            return TranscriptTail(data=b"", total_bytes=0)
        base = workdir or remote_experiment_dir(
            experiment_id=experiment_id, root=self.config.remote_root
        )
        # Read both current and one-release legacy transcript locations.
        abs_path = PurePosixPath(
            remote_sessions_dir(experiment_id=experiment_id, root=remote_root_of(base)),
            TRANSCRIPT_FILENAME,
        ).as_posix()
        legacy_path = PurePosixPath(base, _transcript_rel_path(experiment_id)).as_posix()
        command = transcript_tail_command(paths=[abs_path, legacy_path], limit=limit)
        try:
            sandbox = self._sandbox_from_id(sandbox_id)
            process = sandbox.exec("bash", "-c", command, timeout=20)
            if wait_process(process) != 0:
                return TranscriptTail(data=b"", total_bytes=0)
            return parse_transcript_tail(read_stream(getattr(process, "stdout", None)))
        except Exception:  # noqa: BLE001
            return TranscriptTail(data=b"", total_bytes=0)

    # ---------- modal helpers ----------

    def _sandbox_env(
        self,
        *,
        public_key: str,
        management_public_key: str,
        experiment_id: str,
        workdir: str,
        sandbox_data_dir: str,
    ) -> dict[str, str]:
        env = {
            "RP_AUTHORIZED_KEY": public_key,
            "RP_MANAGEMENT_KEY": management_public_key,
            "RP_EXPERIMENT_ID": experiment_id,
            "RP_WORKDIR": workdir,
            "MERV_EXPERIMENT_DIR": workdir,
            "RP_SANDBOX_DATA_DIR": sandbox_data_dir,
            "RP_SESSION_DIR": remote_sessions_dir(
                experiment_id=experiment_id, root=remote_root_of(workdir)
            ),
        }
        return env

    def _sandbox_secrets(self, modal: Any, *, hf_token: str = "") -> list[Any]:
        """Build per-user secrets without a deployment-wide token fallback."""
        secrets: list[Any] = []
        if hf_token:
            secrets.append(
                modal.Secret.from_dict(
                    {"HF_TOKEN": hf_token, "HUGGING_FACE_HUB_TOKEN": hf_token}
                )
            )
        return secrets

    def _ssh_endpoint(self, *, sandbox: Any) -> tuple[str, int]:
        get_tunnels = getattr(sandbox, "tunnels", None)
        if not callable(get_tunnels):
            raise BackendUnavailableError("Modal sandbox does not expose tunnels()")
        try:
            tunnels = maybe_await(get_tunnels())
            tunnel = tunnels[22]
            socket = getattr(tunnel, "tcp_socket", None)
            if not socket:
                raise BackendUnavailableError("Modal tunnel exposed no tcp_socket for port 22")
            return str(socket[0]), int(socket[1])
        except BackendUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BackendUnavailableError(f"Modal SSH tunnel is unavailable: {exc}") from exc

    def _sandbox_from_id(self, sandbox_id: str) -> Any:
        modal = self._modal_module()
        return modal.Sandbox.from_id(sandbox_id)

    def _get_app(self) -> Any:
        if self._app is None:
            with self._lock:
                if self._app is None:
                    self._app = self._modal_module().App.lookup(
                        self.config.app_name,
                        create_if_missing=True,
                    )
        return self._app

    def _image(self, *, cuda_devel: bool, image_packages: tuple[str, ...]) -> Any:
        base = self._cuda_image_default() if cuda_devel else self._base_image_default()
        if image_packages:
            return base.pip_install(*image_packages)
        return base

    def _base_image_default(self) -> Any:
        if self._base_image is None:
            with self._lock:
                if self._base_image is None:
                    modal = self._modal_module()
                    self._base_image = self._with_ssh(
                            modal.Image.debian_slim(python_version="3.11")
                            .apt_install(*MODAL_APT_PACKAGES)
                            .pip_install("uv")
                            .run_commands(
                                "ln -sf /usr/bin/fdfind /usr/local/bin/fd || true",
                                "uv pip install --system torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121",
                                "uv pip install --system "
                                + " ".join((*ML_PYTHON_PACKAGES, "modal")),
                            )
                    )
        return self._base_image

    def _cuda_image_default(self) -> Any:
        if self._cuda_image is None:
            with self._lock:
                if self._cuda_image is None:
                    modal = self._modal_module()
                    self._cuda_image = self._with_ssh(
                            modal.Image.from_registry(
                                "nvidia/cuda:12.1.1-devel-ubuntu22.04",
                                add_python="3.11",
                            )
                            .apt_install(*MODAL_APT_PACKAGES)
                            .pip_install("uv")
                            .run_commands(
                                "ln -sf /usr/bin/fdfind /usr/local/bin/fd || true",
                                "uv pip install --system torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121",
                                "uv pip install --system "
                                + " ".join((*ML_PYTHON_PACKAGES, "ninja", "modal")),
                            )
                    )
        return self._cuda_image

    def _with_ssh(self, image: Any) -> Any:
        return image.run_commands(
            "mkdir -p /opt/merv",
            _write_file_layer(BOOT_SCRIPT, "/opt/merv/boot.sh"),
            _write_file_layer(REC_SCRIPT, "/opt/merv/rec.sh"),
            _write_file_layer(MERV_RUN_SCRIPT, MERV_RUN_PATH),
            f"chmod +x /opt/merv/boot.sh /opt/merv/rec.sh {MERV_RUN_PATH}",
            f"ln -sf {MERV_RUN_PATH} /usr/local/bin/merv_run",
            # One-version compat shim for the rp_run -> merv_run rename; remove next release.
            f"ln -sf {MERV_RUN_PATH} /usr/local/bin/rp_run",
        )

    def _modal_module(self) -> Any:
        if self._modal is None:
            try:
                import modal  # type: ignore
            except ImportError as exc:
                raise BackendUnavailableError("modal SDK is not installed") from exc
            self._modal = modal
        return self._modal

    def _ensure_credentials(self) -> None:
        import os

        if not os.environ.get("MODAL_TOKEN_ID") or not os.environ.get("MODAL_TOKEN_SECRET"):
            raise BackendUnavailableError(
                "MODAL_TOKEN_ID / MODAL_TOKEN_SECRET are required for Modal execution"
            )

    def _set_tags(self, *, sandbox: Any, tags: Mapping[str, str]) -> None:
        set_tags = getattr(sandbox, "set_tags", None)
        if not callable(set_tags):
            return
        with suppress(Exception):
            set_tags(dict(tags))


def _transcript_rel_path(experiment_id: str) -> str:
    safe = experiment_id or "unknown"
    return PurePosixPath(SESSIONS_DIR_NAME, safe, TRANSCRIPT_FILENAME).as_posix()


def _sandbox_name(experiment_id: str) -> str | None:
    if not experiment_id:
        return None
    import re

    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", experiment_id).strip("-")
    return f"rp-{safe or 'exp'}"[:63]


def _write_file_layer(content: str, path: str) -> str:
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return f"printf %s '{encoded}' | base64 -d > {shlex.quote(path)}"


def build_modal_sandbox_backend(
    *,
    repo_root: Path,
    activity: ActivityHook | None = None,
) -> ModalSandboxBackend:
    return ModalSandboxBackend(
        repo_root=repo_root,
        config=ModalConfig.from_env(),
        activity=activity,
    )

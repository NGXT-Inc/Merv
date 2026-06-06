"""Modal sandbox backend: procure SSH-wired sandboxes, no job protocol.

Implements the SandboxBackend protocol. The registry (SandboxService) decides
reuse-vs-create policy; this layer only knows how to:

  - acquire(): create one Modal sandbox wired for SSH over an unencrypted tunnel,
    with the project Volume mounted and the agent's public key authorized;
  - is_alive(): poll a sandbox by id;
  - terminate(): stop a sandbox by id;
  - read_transcript(): read the experiment's terminal transcript, live from the
    sandbox first and from the committed Volume as a fallback;
  - health(): is Modal reachable.

The Volume + bidirectional sync subsystem is reused unchanged from the job-era
backend (see sync/sync.md).
"""

from __future__ import annotations

import base64
import os
import shlex
import threading
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from backend.sync_config import SyncExclusionPolicy
from ...errors import BackendUnavailableError, BackendValidationError
from ...types import (
    BackendCapabilities,
    OnCreated,
    OnPhase,
    ProvisionedSandbox,
    SandboxRequest,
)
from .config import ModalConfig
from ._sandbox_ops import maybe_await, read_stream, wait_process
from .sync import BaselineStore, SyncEngine, SyncPoller


ActivityHook = Callable[[str, dict[str, Any]], None]
ShouldPollProject = Callable[[str], bool]
SyncExclusionProvider = Callable[[str], SyncExclusionPolicy]

SESSIONS_DIR_NAME = ".research_plugin_sessions"
TRANSCRIPT_FILENAME = "transcript.log"
TRANSCRIPT_TAIL_DEFAULT = 50_000

# How long a metrics sample exec may run before we give up. The sampler sleeps
# ~0.25s (for the CPU delta) plus a single nvidia-smi call, so this is generous.
METRICS_EXEC_TIMEOUT = 15


# Read-only usage sampler run via `sandbox.exec` (control-plane exec, so it
# bypasses the sshd ForceCommand transcript wrapper — same as the transcript
# reader). Emits machine-parseable `RPM <key>=<value>` lines for the in-container
# gauges: CPU cores in use (a two-point cgroup delta), memory in use (anonymous
# RSS from /proc/meminfo — see the memory block for why cgroup/meminfo limits are
# unusable under gVisor), and per-GPU utilization + VRAM via nvidia-smi. Every
# probe degrades to silence rather than failing, so a CPU-only sandbox (no
# nvidia-smi) still returns what it can.
METRICS_SCRIPT = r"""
set -u
now_ns() { date +%s%N; }
# Cumulative CPU time in microseconds (cgroup v2 usage_usec, else v1 cpuacct in
# ns / 1000). NOTE: the sandbox's awk is mawk, whose printf "%d" is 32-bit and
# silently clamps at INT_MAX (2147483647) — a cumulative counter blows past that
# in well under an hour, which would clamp BOTH samples to the same value and
# report 0 cores. Use %.0f (double-backed) for the conversion to stay exact.
cpu_usage_usec() {
  if [ -r /sys/fs/cgroup/cpu.stat ]; then
    awk '/^usage_usec/{print $2; exit}' /sys/fs/cgroup/cpu.stat
  elif [ -r /sys/fs/cgroup/cpuacct/cpuacct.usage ]; then
    awk '{printf "%.0f", $1/1000}' /sys/fs/cgroup/cpuacct/cpuacct.usage
  fi
}
u1=$(cpu_usage_usec); t1=$(now_ns)
sleep 0.25
u2=$(cpu_usage_usec); t2=$(now_ns)
if [ -n "${u1:-}" ] && [ -n "${u2:-}" ]; then
  awk -v a="$u1" -v b="$u2" -v ta="$t1" -v tb="$t2" \
    'BEGIN{ d=tb-ta; if(d>0) printf "RPM cpu_cores_used=%.4f\n", ((b-a)*1000.0)/d }'
fi
if [ -r /sys/fs/cgroup/cpu.max ]; then
  read -r q p < /sys/fs/cgroup/cpu.max || true
  if [ "${q:-max}" != "max" ] && [ -n "${p:-}" ]; then
    awk -v q="$q" -v p="$p" 'BEGIN{ if(p>0) printf "RPM cpu_cores_limit=%.4f\n", q/p }'
  fi
fi
# Memory used. Modal runs sandboxes under gVisor, where the per-container memory
# cgroup is NOT projected in (the root cgroup and /proc/meminfo report host-level
# totals), so cgroup usage/limit are useless here. Derive "used" as anonymous +
# unreclaimable memory = MemTotal - MemFree - Buffers - Cached - SReclaimable.
# This deliberately excludes the reclaimable page cache that a memory-mapped
# dataset inflates "used" with (it would otherwise read as ~all of host RAM and
# isn't real memory pressure). The denominator is the reserved request, which the
# backend supplies — we intentionally do NOT emit a limit from these host files.
if [ -r /proc/meminfo ]; then
  awk '
    /^MemTotal:/      {t=$2}
    /^MemFree:/       {f=$2}
    /^Buffers:/       {b=$2}
    /^Cached:/        {c=$2}
    /^SReclaimable:/  {s=$2}
    END { u=t-f-b-c-s; if (u<0) u=0; printf "RPM mem_used_bytes=%.0f\n", u*1024 }
  ' /proc/meminfo
fi
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total,name \
    --format=csv,noheader,nounits 2>/dev/null | \
  while IFS=',' read -r idx util used total name; do
    trim() { echo "$1" | sed 's/^ *//; s/ *$//'; }
    printf 'RPM gpu idx=%s util=%s used=%s total=%s name=%s\n' \
      "$(trim "$idx")" "$(trim "$util")" "$(trim "$used")" "$(trim "$total")" "$(trim "$name")"
  done
fi
echo "RPM ok=1"
"""


# Entrypoint baked into every sandbox image. Authorizes the registry-owned key,
# generates host keys, writes an sshd_config whose ForceCommand is the transcript
# wrapper, then execs sshd in the foreground (which keeps the container alive).
BOOT_SCRIPT = r"""#!/usr/bin/env bash
set -eu
mkdir -p "${RP_SANDBOX_DATA_DIR:-/workspace/sandbox_data}"
mkdir -p /root/.ssh && chmod 700 /root/.ssh
if [ -n "${RP_AUTHORIZED_KEY:-}" ]; then
  printf '%s\n' "$RP_AUTHORIZED_KEY" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
fi
# Persist the session env so the ForceCommand wrapper can read it (sshd does not
# pass the container environment through to forced commands).
{
  printf 'RP_WORKDIR=%q\n' "${RP_WORKDIR:-/workspace/repo}"
  printf 'RP_EXPERIMENT_ID=%q\n' "${RP_EXPERIMENT_ID:-unknown}"
  printf 'RP_SANDBOX_DATA_DIR=%q\n' "${RP_SANDBOX_DATA_DIR:-/workspace/sandbox_data}"
  printf 'RP_DATASET_DIR=%q\n' "${RP_SANDBOX_DATA_DIR:-/workspace/sandbox_data}"
  if [ -n "${HF_TOKEN:-}" ]; then
    printf 'HF_TOKEN=%q\n' "$HF_TOKEN"
    printf 'HUGGING_FACE_HUB_TOKEN=%q\n' "${HUGGING_FACE_HUB_TOKEN:-$HF_TOKEN}"
  fi
} > /opt/rp/env
mkdir -p /run/sshd
ssh-keygen -A >/dev/null 2>&1 || true
cat > /etc/ssh/sshd_config <<'EOF'
Port 22
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
AuthorizedKeysFile /root/.ssh/authorized_keys
ForceCommand /opt/rp/rec.sh
PrintMotd no
AcceptEnv LANG LC_*
PidFile /run/sshd.pid
EOF
exec /usr/sbin/sshd -D -e
"""


# ForceCommand wrapper: records every SSH channel (interactive shell or
# `ssh host 'cmd'`) to a per-experiment transcript on the mounted Volume while
# still streaming output back to the agent. Exit code is preserved.
REC_SCRIPT = r"""#!/usr/bin/env bash
[ -f /opt/rp/env ] && . /opt/rp/env
RP_WORKDIR="${RP_WORKDIR:-/workspace/repo}"
RP_EXPERIMENT_ID="${RP_EXPERIMENT_ID:-unknown}"
RP_SANDBOX_DATA_DIR="${RP_SANDBOX_DATA_DIR:-/workspace/sandbox_data}"
RP_DATASET_DIR="${RP_DATASET_DIR:-$RP_SANDBOX_DATA_DIR}"
if [ -n "${HF_TOKEN:-}" ] && [ -z "${HUGGING_FACE_HUB_TOKEN:-}" ]; then
  HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"
fi
export RP_WORKDIR RP_EXPERIMENT_ID RP_SANDBOX_DATA_DIR RP_DATASET_DIR HF_TOKEN HUGGING_FACE_HUB_TOKEN
mkdir -p "$RP_SANDBOX_DATA_DIR" 2>/dev/null || true
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


class ModalSandboxBackend:
    capabilities = BackendCapabilities(name="modal")

    def __init__(
        self,
        *,
        repo_root: Path,
        config: ModalConfig | None = None,
        modal_module: Any | None = None,
        activity: ActivityHook | None = None,
        sync_engine: SyncEngine | None = None,
        baseline: BaselineStore | None = None,
        poller_interval_seconds: float = 60.0,
        start_poller: bool = True,
        should_poll_project: ShouldPollProject | None = None,
        sync_exclusion_provider: SyncExclusionProvider | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.config = config or ModalConfig.from_env()
        self.activity = activity
        self._modal = modal_module
        self._app = None
        self._base_image = None
        self._cuda_image = None
        self._lock = threading.Lock()
        self._volume_objects: dict[str, Any] = {}

        if sync_engine is not None:
            self.baseline = baseline if baseline is not None else sync_engine.baseline
            self.sync_engine = sync_engine
        else:
            sync_db = self.repo_root / ".research_plugin" / "modal" / "sync.sqlite"
            self.baseline = baseline or BaselineStore(db_path=sync_db)
            self.sync_engine = SyncEngine(
                repo_root=self.repo_root,
                baseline=self.baseline,
                volume_provider=self._provide_volume,
                volume_name_prefix=self.config.volume_name_prefix,
                volume_mount_path=self.config.remote_workdir,
                activity=self.activity,
                exclusion_provider=sync_exclusion_provider,
            )
        if sync_exclusion_provider is not None:
            self.sync_engine.set_exclusion_provider(sync_exclusion_provider)
        self.poller = SyncPoller(
            engine=self.sync_engine,
            baseline=self.baseline,
            interval_seconds=poller_interval_seconds,
            activity=self.activity,
            should_sync_project=should_poll_project,
        )
        if start_poller:
            self.poller.start()

    def set_sync_exclusion_provider(
        self,
        provider: SyncExclusionProvider | None,
    ) -> None:
        self.sync_engine.set_exclusion_provider(provider)

    # ---------- SandboxBackend protocol ----------

    def acquire(
        self,
        *,
        request: SandboxRequest,
        on_phase: OnPhase | None = None,
        on_created: OnCreated | None = None,
    ) -> ProvisionedSandbox:
        self._ensure_credentials()
        _call(on_phase, "syncing", "ensuring project volume")
        info = self.sync_engine.ensure_project_volume(project_id=request.project_id)
        volume_name = info["volume_name"]
        # Push current repo state to the Volume before the sandbox boots so the
        # agent sees up-to-date code/configs. This can be the slow step on a
        # large first-time delta — which is exactly why provisioning runs in the
        # background and the agent polls.
        _call(on_phase, "syncing", "pushing repo to volume")
        try:
            self.sync_engine.sync(project_id=request.project_id)
        except Exception:  # noqa: BLE001 — sync is best-effort at acquire time
            pass

        workdir = request.remote_workdir or self.config.remote_workdir
        sandbox_data_dir = self.config.sandbox_data_dir
        volume = self._provide_volume(volume_name)
        modal = self._modal_module()
        image = self._image(cuda_devel=request.cuda_devel, image_packages=request.image_packages)
        app = self._get_app()
        env = self._sandbox_env(
            public_key=request.public_key,
            experiment_id=request.experiment_id,
            workdir=workdir,
            sandbox_data_dir=sandbox_data_dir,
        )
        secrets = self._sandbox_secrets(modal)
        name = _sandbox_name(request.experiment_id)
        kwargs: dict[str, Any] = {
            "app": app,
            "image": image,
            "timeout": int(request.time_limit),
            "workdir": workdir,
            "volumes": {workdir: volume},
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
        _call(on_phase, "creating", f"gpu={request.gpu or 'cpu'}")
        try:
            sandbox = modal.Sandbox.create("bash", "/opt/rp/boot.sh", **kwargs)
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
            # Hand the id back so the registry persists it before the slow tunnel
            # wait. May raise to cancel.
            _call(on_created, sandbox_id, name or "")
            _call(on_phase, "connecting", "waiting for ssh")
            host, port = self._ssh_endpoint(sandbox=sandbox)
        except BaseException:
            try:
                sandbox.terminate()
            except Exception:  # noqa: BLE001
                pass
            raise
        return ProvisionedSandbox(
            sandbox_id=sandbox_id,
            ssh_host=host,
            ssh_port=port,
            ssh_user="root",
            workdir=workdir,
            volume_name=volume_name,
            sandbox_data_dir=sandbox_data_dir,
            reused=False,
        )

    def find_sandbox_id(self, *, experiment_id: str) -> str | None:
        """Best-effort lookup of a sandbox we created for this experiment by name.

        Used by the registry to reconcile a provisioning row whose daemon-side
        job died before it recorded the sandbox id (e.g. a restart mid-provision)
        — the orphan still holds the deterministic name, so we can find and adopt
        or clean it up. Returns None if no such sandbox exists.
        """
        name = _sandbox_name(experiment_id)
        if not name:
            return None
        try:
            modal = self._modal_module()
            sandbox = modal.Sandbox.from_name(self.config.app_name, name)
            return str(getattr(sandbox, "object_id", "") or "") or None
        except Exception:  # noqa: BLE001 — not found / unreachable
            return None

    def is_alive(self, *, sandbox_id: str) -> bool:
        if not sandbox_id:
            return False
        try:
            sandbox = self._sandbox_from_id(sandbox_id)
            poll = getattr(sandbox, "poll", None)
            if not callable(poll):
                return True
            return maybe_await(poll()) is None
        except Exception:  # noqa: BLE001
            return False

    def refresh_ssh_endpoint(self, *, sandbox_id: str) -> tuple[str, int] | None:
        """Re-read the live SSH tunnel endpoint for an existing sandbox.

        Lets the registry recover from a tunnel that moved without recreating
        the sandbox (is_alive only proves the control plane is up, not that the
        cached `rNNN.modal.host:port` still routes). Best-effort: returns None
        if the sandbox is gone or its tunnel can't be read right now.
        """
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
        ok = False
        try:
            sandbox.terminate()
            ok = True
        except Exception:  # noqa: BLE001
            pass
        try:
            detach = getattr(sandbox, "detach", None)
            if callable(detach):
                detach()
        except Exception:  # noqa: BLE001
            pass
        return ok

    def read_transcript(
        self,
        *,
        sandbox_id: str,
        experiment_id: str,
        volume_name: str,
        workdir: str,
        tail: int | None = None,
    ) -> str:
        limit = int(tail) if tail and tail > 0 else TRANSCRIPT_TAIL_DEFAULT
        rel_path = _transcript_rel_path(experiment_id)
        live = self._read_transcript_live(
            sandbox_id=sandbox_id,
            workdir=workdir,
            rel_path=rel_path,
            limit=limit,
        )
        if live:
            return live
        return self._read_transcript_volume(
            volume_name=volume_name,
            rel_path=rel_path,
            limit=limit,
        )

    def sample_metrics(self, *, sandbox_id: str) -> dict[str, Any] | None:
        """Sample live in-container usage (CPU/RAM/GPU) via a read-only exec.

        Returns a parsed gauge dict, or None when the sandbox is unreachable or
        the sampler produced nothing usable. Never raises — the registry treats
        a None as "metrics unavailable" and the UI hides the strip.
        """
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
        return _parse_metrics(output)

    def sync_sandbox_files(
        self,
        *,
        project_id: str,
        sandbox_id: str,
        workdir: str,
        volume_name: str,
    ) -> dict[str, Any]:
        """Commit mounted Volume writes from the live sandbox, then sync local."""
        if not sandbox_id:
            raise BackendUnavailableError("cannot sync without a live sandbox id")
        if not workdir:
            workdir = self.config.remote_workdir
        try:
            sandbox = self._sandbox_from_id(sandbox_id)
            process = sandbox.exec("sync", workdir, timeout=300)
            exit_code = wait_process(process)
            if exit_code != 0:
                stderr = read_stream(getattr(process, "stderr", None))
                stdout = read_stream(getattr(process, "stdout", None))
                detail = stderr or stdout or "no output"
                raise BackendUnavailableError(
                    f"Modal Volume commit via sync failed with exit code {exit_code}: {detail}"
                )
        except BackendUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BackendUnavailableError(f"Modal sandbox sync failed: {exc}") from exc

        if volume_name:
            self._provide_volume(volume_name)
        result = self.sync_engine.sync(project_id=project_id)
        return {
            "project_id": project_id,
            "sandbox_id": sandbox_id,
            "workdir": workdir,
            "volume": volume_name,
            "committed": True,
            "pushed": result.pushed,
            "pulled": result.pulled,
            "deleted_remote": result.deleted_remote,
            "deleted_local": result.deleted_local,
            "conflicts": result.conflicts,
            "skipped_conflicts": list(result.skipped_conflicts),
            "skipped_busy": result.skipped_busy,
            "coalesced": result.coalesced,
            "duration_ms": result.duration_ms,
        }

    def health(self) -> dict[str, Any]:
        try:
            self._ensure_credentials()
            self._get_app()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "name": "modal", "error": str(exc)}
        return {"ok": True, "name": "modal", "app": self.config.app_name}

    def sandbox_environment(self) -> dict[str, Any]:
        available_tokens: list[str] = []
        if os.environ.get("HF_TOKEN"):
            available_tokens.append("HF_TOKEN")
        return {
            "available_tokens": available_tokens,
            "notes": (
                [
                    "HF_TOKEN is available inside the sandbox for Hugging Face downloads. "
                    "Do not print or write the token; use it through Hugging Face tooling."
                ]
                if available_tokens
                else []
            ),
        }

    def on_project_created(self, *, project_id: str) -> None:
        self.sync_engine.ensure_project_volume(project_id=project_id)

    def shutdown(self) -> None:
        try:
            self.poller.stop()
        except Exception:  # noqa: BLE001
            pass

    # ---------- transcript helpers ----------

    def _read_transcript_live(
        self, *, sandbox_id: str, workdir: str, rel_path: str, limit: int
    ) -> str:
        if not sandbox_id:
            return ""
        abs_path = PurePosixPath(workdir, rel_path).as_posix()
        command = (
            f"if [ -f {shlex.quote(abs_path)} ]; then "
            f"tail -c {int(limit)} {shlex.quote(abs_path)}; fi"
        )
        try:
            sandbox = self._sandbox_from_id(sandbox_id)
            process = sandbox.exec("bash", "-c", command, timeout=20)
            if wait_process(process) != 0:
                return ""
            return read_stream(getattr(process, "stdout", None))
        except Exception:  # noqa: BLE001
            return ""

    def _read_transcript_volume(self, *, volume_name: str, rel_path: str, limit: int) -> str:
        if not volume_name:
            return ""
        try:
            volume = self._provide_volume(volume_name)
            chunks: list[bytes] = []
            for chunk in volume.read_file(rel_path):
                chunks.append(chunk if isinstance(chunk, bytes) else bytes(chunk))
            data = b"".join(chunks)
            if len(data) > limit:
                data = data[-limit:]
            return data.decode("utf-8", errors="replace")
        except FileNotFoundError:
            return ""
        except Exception:  # noqa: BLE001
            return ""

    # ---------- modal helpers ----------

    def _provide_volume(self, volume_name: str) -> Any:
        volume = self._volume_objects.get(volume_name)
        if volume is None:
            self._ensure_credentials()
            modal = self._modal_module()
            try:
                volume = modal.Volume.from_name(
                    volume_name,
                    create_if_missing=True,
                    version=self.config.volume_version,
                )
            except Exception as exc:  # noqa: BLE001
                raise BackendUnavailableError(
                    f"Modal volume is unavailable: {volume_name}: {exc}"
                ) from exc
            self._volume_objects[volume_name] = volume
        reload = getattr(volume, "reload", None)
        if callable(reload):
            try:
                reload()
            except Exception:  # noqa: BLE001
                pass
        return volume

    def _sandbox_env(
        self,
        *,
        public_key: str,
        experiment_id: str,
        workdir: str,
        sandbox_data_dir: str,
    ) -> dict[str, str]:
        return {
            "RP_AUTHORIZED_KEY": public_key,
            "RP_EXPERIMENT_ID": experiment_id,
            "RP_WORKDIR": workdir,
            "RP_SANDBOX_DATA_DIR": sandbox_data_dir,
        }

    def _sandbox_secrets(self, modal: Any) -> list[Any]:
        """Build Modal sandbox secrets from the daemon environment.

        The backend has already loaded the configured env file into
        ``os.environ``. Use Modal's local-environment helper instead of
        ``Secret.from_dotenv()`` so sandbox creation is independent of the
        daemon's current working directory.
        """
        if not os.environ.get("HF_TOKEN"):
            return []
        keys = ["HF_TOKEN"]
        if os.environ.get("HUGGING_FACE_HUB_TOKEN"):
            keys.append("HUGGING_FACE_HUB_TOKEN")
        return [modal.Secret.from_local_environ(keys)]

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
                        .apt_install("openssh-server", "ca-certificates", "curl")
                        .pip_install("uv")
                        .run_commands(
                            "uv pip install --system torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121",
                            "uv pip install --system transformers numpy matplotlib pandas scikit-learn modal",
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
                        .apt_install("openssh-server", "ca-certificates", "curl")
                        .pip_install("uv")
                        .run_commands(
                            "uv pip install --system torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121",
                            "uv pip install --system transformers numpy matplotlib pandas scikit-learn ninja modal",
                        )
                    )
        return self._cuda_image

    def _with_ssh(self, image: Any) -> Any:
        """Bake the SSH entrypoint + transcript wrapper into the image."""
        return image.run_commands(
            "mkdir -p /opt/rp",
            _write_file_layer(BOOT_SCRIPT, "/opt/rp/boot.sh"),
            _write_file_layer(REC_SCRIPT, "/opt/rp/rec.sh"),
            "chmod +x /opt/rp/boot.sh /opt/rp/rec.sh",
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
        try:
            set_tags(dict(tags))
        except Exception:  # noqa: BLE001
            pass


def _call(cb: Any, *args: Any) -> None:
    """Invoke a progress callback if present; let it raise (to cancel)."""
    if cb is not None:
        cb(*args)


def _to_float(value: str | None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_int(value: str | None) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _parse_gpu(body: str) -> dict[str, Any] | None:
    """Parse one `idx=.. util=.. used=.. total=.. name=..` GPU line."""
    name = ""
    head = body
    if " name=" in body:
        head, name = body.split(" name=", 1)
    fields: dict[str, str] = {}
    for token in head.split():
        if "=" in token:
            key, val = token.split("=", 1)
            fields[key] = val
    index = _to_int(fields.get("idx"))
    if index is None:
        return None
    return {
        "index": index,
        "name": name.strip(),
        "util_pct": _to_int(fields.get("util")),
        "mem_used_mib": _to_int(fields.get("used")),
        "mem_total_mib": _to_int(fields.get("total")),
    }


def _parse_metrics(output: str) -> dict[str, Any] | None:
    """Turn `RPM key=value` sampler lines into a structured gauge dict."""
    cpu_used = cpu_limit = None
    mem_used = mem_limit = None
    gpus: list[dict[str, Any]] = []
    saw_ok = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("RPM "):
            continue
        body = line[4:]
        if body.startswith("cpu_cores_used="):
            cpu_used = _to_float(body.split("=", 1)[1])
        elif body.startswith("cpu_cores_limit="):
            cpu_limit = _to_float(body.split("=", 1)[1])
        elif body.startswith("mem_used_bytes="):
            mem_used = _to_int(body.split("=", 1)[1])
        elif body.startswith("mem_limit_bytes="):
            mem_limit = _to_int(body.split("=", 1)[1])
        elif body.startswith("gpu "):
            gpu = _parse_gpu(body[4:])
            if gpu is not None:
                gpus.append(gpu)
        elif body.startswith("ok="):
            saw_ok = body.split("=", 1)[1].strip() == "1"
    if not saw_ok and cpu_used is None and mem_used is None and not gpus:
        return None
    return {
        "cpu": {"used_cores": cpu_used, "limit_cores": cpu_limit},
        "memory": {"used_bytes": mem_used, "limit_bytes": mem_limit},
        "gpus": gpus,
    }


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
    should_poll_project: ShouldPollProject | None = None,
    sync_exclusion_provider: SyncExclusionProvider | None = None,
) -> ModalSandboxBackend:
    return ModalSandboxBackend(
        repo_root=repo_root,
        config=ModalConfig.from_env(),
        activity=activity,
        should_poll_project=should_poll_project,
        sync_exclusion_provider=sync_exclusion_provider,
    )

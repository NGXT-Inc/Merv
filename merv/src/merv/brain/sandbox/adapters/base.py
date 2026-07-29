# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Provider-neutral Sandbox contract and shared adapter mechanics."""

from __future__ import annotations

import json
import os
import re
import socket
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from ...kernel.env import env_value
from ..remote.bootstrap_tools import ML_PYTHON_PACKAGES
from ..remote.vm_bootstrap import build_standard_user_data
from ..remote.vm_ssh import (
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
from ..sandbox_paths import SESSIONS_DIRNAME, remote_experiment_dir, remote_root_of, remote_sessions_dir


from ..models import (
    BackendCapabilities,
    BackendUnavailableError,
    BackendValidationError,
    CapacityUnavailableError,
    OnCreated,
    OnPhase,
    ProvisionedSandbox,
    SandboxBackendBase,
    SandboxRequest,
    SandboxTarget,
    TranscriptTail,
)


# Configuration helpers

def _first_env(*names: str) -> str:
    for name in names:
        value = env_value(name)
        if value:
            return value
    return ""


def _required_env(*names: str, error: str) -> str:
    value = _first_env(*names)
    if not value:
        raise BackendValidationError(error)
    return value


def _http_base_url(name: str, default: str) -> str:
    value = env_value(name) or default
    if not value.startswith(("http://", "https://")):
        raise BackendValidationError(f"{name} must be an HTTP URL")
    return value.rstrip("/")


def _env_discovery_disabled() -> bool:
    """True in control mode, where implicit user-machine .env discovery is off.

    Reads MERV_MODE directly (no merv.brain.surface.config import) to keep the
    execution backends loosely coupled from the composition layer. Local mode
    keeps checkout-adjacent .env discovery for development; control resolves
    credentials from the process environment or secret store only.
    """
    return (env_value("MERV_MODE") or "").lower() == "control"


def _load_env_text(text: str) -> None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _absolute_posix_path(value: str, *, field: str) -> str:
    value = value.strip()
    if not value.startswith("/"):
        raise BackendValidationError(f"{field} must be an absolute POSIX path")
    return value.rstrip("/") or "/"


def _is_under_path(child: str, parent: str) -> bool:
    child = child.rstrip("/")
    parent = parent.rstrip("/")
    return child == parent or child.startswith(parent + "/")


def _validate_data_dir(data_dir: str, *, remote_root: str, field: str) -> None:
    """The data dir may live under the remote root (e.g. /workspace/data), but
    must never collide with the locations the plugin manages there: the
    per-experiment synced folders (``<root>/exp_*``) and the sessions tree."""
    root = remote_root.rstrip("/")
    if data_dir.rstrip("/") == root:
        raise BackendValidationError(f"{field} must not equal the remote root {root}")
    if _is_under_path(data_dir, root):
        first = data_dir.rstrip("/")[len(root) + 1 :].split("/", 1)[0]
        if first.startswith("exp_") or first == SESSIONS_DIRNAME:
            raise BackendValidationError(
                f"{field} must not collide with per-experiment folders or "
                f"{SESSIONS_DIRNAME} under the remote root"
            )


def _positive_int(value: object, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise BackendValidationError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise BackendValidationError(f"{field} must be a positive integer")
    return parsed


def _positive_float(value: object, *, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise BackendValidationError(f"{field} must be positive") from exc
    if parsed <= 0:
        raise BackendValidationError(f"{field} must be positive")
    return parsed


# HTTP helpers

def bearer_json_headers(token: str, user_agent: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    }


def request_json(
    *,
    provider: str,
    method: str,
    base_url: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
    require_object: bool = False,
    report_http_status: bool = True,
) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(f"{base_url}{path}", data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise BackendUnavailableError(
            f"{provider} API {method} {path} failed with HTTP {exc.code}: {detail}",
            status=exc.code if report_http_status else None,
        ) from exc
    except URLError as exc:
        raise BackendUnavailableError(f"{provider} API is unreachable: {exc}") from exc
    except TimeoutError as exc:
        raise BackendUnavailableError(f"{provider} API request timed out") from exc
    try:
        parsed = json.loads(payload) if payload else {}
    except json.JSONDecodeError as exc:
        raise BackendUnavailableError(f"{provider} API returned invalid JSON") from exc
    if require_object and not isinstance(parsed, dict):
        raise BackendUnavailableError(f"{provider} API returned a non-object response")
    return parsed


# Catalog value helpers

def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _float_or_none(value: Any) -> float | None:
    """Keep missing, malformed, negative, and NaN prices unknown.

    Coercing any of them to zero would bypass spend ceilings; explicit provider
    zero remains a known price.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price != price or price < 0:  # NaN and negatives are not prices
        return None
    return price


def price_sort_key(option: dict[str, Any]) -> tuple[bool, float, str]:
    """Cheapest first, with unknown prices last."""
    price = option.get("price_usd_per_hour")
    return (
        price is None,
        float(price if price is not None else 0.0),
        str(option.get("instance_type") or ""),
    )


def find_option(
    options: list[dict[str, Any]], *, instance_type: str
) -> dict[str, Any] | None:
    wanted = _norm(instance_type)
    for option in options:
        if _norm(option.get("instance_type")) == wanted:
            return option
    return None


# Shared VM and SSH behavior

def _vm_name(experiment_id: str, *, max_length: int = 60) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", experiment_id.lower()).strip("-")
    return f"rp-{safe or 'exp'}"[:max_length]


class VmSshSandboxBackend(SandboxBackendBase):
    """Common management-channel behavior for provisioned VM backends."""

    resource_label = "VM"
    live_statuses: frozenset[str] | None = None
    ready_statuses: frozenset[str] = frozenset()
    terminal_statuses: frozenset[str] = frozenset()

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
        target: SandboxTarget,
        tail: int | None = None,
    ) -> TranscriptTail:
        """Tail exact bytes without recording the management read itself."""
        return read_transcript_via_mgmt_ssh(
            ssh_runner=self._ssh_runner,
            sandbox_id=target.sandbox_id,
            experiment_id=target.experiment_id,
            workdir=target.workdir,
            remote_root=self.config.remote_root,
            ssh_host=target.ssh_host,
            ssh_port=target.ssh_port,
            key_path=target.key_path,
            tail=tail,
        )

    def sample_metrics(
        self,
        *,
        target: SandboxTarget,
    ) -> dict[str, Any] | None:
        """Sample gauges without recording management polling."""
        return sample_metrics_via_mgmt_ssh(
            ssh_runner=self._ssh_runner,
            sandbox_id=target.sandbox_id,
            ssh_host=target.ssh_host,
            ssh_port=target.ssh_port,
            key_path=target.key_path,
        )

    def read_runs(
        self,
        *,
        target: SandboxTarget,
    ) -> list[dict[str, Any]] | None:
        """Return receipts; ``[]`` is empty and ``None`` means no news."""
        return read_runs_via_mgmt_ssh(
            ssh_runner=self._ssh_runner,
            sandbox_id=target.sandbox_id,
            workdir=target.workdir,
            ssh_host=target.ssh_host,
            ssh_port=target.ssh_port,
            key_path=target.key_path,
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

    def _get_resource(self, sandbox_id: str) -> dict[str, Any]:
        """Fetch one provider VM. Concrete adapters keep endpoint quirks."""
        raise NotImplementedError

    def _resource_status(self, resource: Mapping[str, Any]) -> str:
        return str(resource.get("status") or "")

    def _resource_is_addressable(self, resource: Mapping[str, Any]) -> bool:
        raise NotImplementedError

    def _status_is_live(self, status: str) -> bool:
        if self.live_statuses is not None:
            return status in self.live_statuses
        return status not in self.terminal_statuses

    def is_alive(self, *, sandbox_id: str) -> bool:
        """A 404 is gone; every other provider failure remains unknown."""
        if not sandbox_id:
            return False
        try:
            resource = self._get_resource(sandbox_id)
        except BackendUnavailableError as exc:
            if exc.status == 404:
                return False
            raise
        return self._status_is_live(self._resource_status(resource))

    def _find_named_resource_id(
        self,
        *,
        name: str,
        resources: Iterable[Mapping[str, Any]],
        name_field: str = "name",
    ) -> str | None:
        """A successful listing can authoritatively prove absence."""
        for resource in resources:
            if (
                str(resource.get(name_field) or "") == name
                and self._status_is_live(self._resource_status(resource))
            ):
                return str(resource.get("id") or "") or None
        return None

    def _wait_for_vm(self, *, sandbox_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.poll_timeout_seconds
        last_status = ""
        while time.monotonic() < deadline:
            resource = self._get_resource(sandbox_id)
            last_status = self._resource_status(resource)
            if (
                last_status in self.ready_statuses
                and self._resource_is_addressable(resource)
            ):
                return resource
            if last_status in self.terminal_statuses:
                raise BackendUnavailableError(
                    f"{self.resource_label} {sandbox_id} reached terminal status "
                    f"{last_status or 'unknown'}"
                )
            time.sleep(self.config.poll_interval_seconds)
        raise BackendUnavailableError(
            f"{self.resource_label} {sandbox_id} did not become ready before timeout "
            f"(last status: {last_status or 'unknown'})"
        )

    @staticmethod
    def _delete_with_404(
        *, sandbox_id: str, delete: Callable[[str], Any]
    ) -> bool:
        """Delete once; only an authoritative 404 also counts as success."""
        if not sandbox_id:
            return False
        try:
            delete(sandbox_id)
        except BackendUnavailableError as exc:
            return exc.status == 404
        except Exception:  # noqa: BLE001 -- cleanup retries own recovery
            return False
        return True

    def write_secrets(
        self,
        *,
        target: SandboxTarget,
        secrets: Mapping[str, str],
    ) -> bool:
        """Deliver secrets after boot; failure cannot fail provisioning."""
        return write_secrets_via_mgmt_ssh(
            ssh_runner=self._ssh_input_runner,
            sandbox_id=target.sandbox_id,
            secrets=secrets,
            ssh_host=target.ssh_host,
            ssh_port=target.ssh_port,
            key_path=target.key_path,
        )

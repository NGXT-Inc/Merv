# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Provider-neutral sandbox backend port, request types, and errors."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable, Mapping, Protocol, runtime_checkable


SANDBOX_STATES = ("provisioning", "running", "terminated", "failed", "unknown")


class ExecutionBackendError(Exception):
    """Backend error with optional machine-readable details."""

    def __init__(self, message: str = "", *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class BackendValidationError(ExecutionBackendError):
    """Caller-supplied sandbox spec or backend hints are malformed."""


class BackendPermissionError(ExecutionBackendError):
    """Caller-supplied sandbox spec or environment violates execution policy."""


class BackendUnavailableError(ExecutionBackendError):
    """The selected backend cannot be reached or initialized.

    ``status`` carries the HTTP status when the provider answered at all
    (None = no answer), so liveness checks can separate "instance gone"
    from "provider down".
    """

    def __init__(
        self,
        message: str = "",
        *,
        status: int | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.status = status


class CapacityUnavailableError(BackendUnavailableError):
    """Retryable lack of stock, distinct from provider outage."""


# Persist the native ID before the slow SSH wait.
OnPhase = Callable[[str, str], None]      # (phase, detail)
OnCreated = Callable[[str, str], None]    # (sandbox_id, sandbox_name)


@dataclass(frozen=True)
class TranscriptTail:
    """Tail bytes plus the full transcript size.

    Keep ``data`` undecoded so multibyte text cannot skew absolute byte cursors.
    """

    data: bytes = b""
    total_bytes: int = 0


@dataclass(frozen=True)
class SandboxRequest:
    """A request to procure one SSH-reachable sandbox."""

    experiment_id: str
    project_id: str
    public_key: str
    sandbox_uid: str = ""
    public_key_source: str = "managed"
    management_public_key: str = ""
    management_key_path: str = ""
    gpu: str | None = None
    cpu: float = 2.0
    memory: int = 8192
    time_limit: int = 3600
    image_packages: tuple[str, ...] = ()
    cuda_devel: bool = False
    remote_workdir: str = ""
    instance_type: str | None = None
    region: str | None = None
    # None selects the configured default provider.
    provider: str | None = None
    # Modal injects this token at provision; VM providers deliver it post-boot.
    hf_token: str = ""
    # Management-key ID for generation spend attribution.
    key_id: str = ""


@dataclass(frozen=True)
class ProvisionedSandbox:
    sandbox_id: str
    ssh_host: str
    ssh_port: int
    ssh_user: str
    workdir: str
    volume_name: str
    sync_dir: str = ""
    unsynced_dir: str = ""
    sandbox_data_dir: str = ""
    reused: bool = False
    gpu: str = ""
    cpu: float | None = None
    memory: int | None = None
    instance_type: str = ""
    region: str = ""
    price_usd_per_hour: float = 0.0


@dataclass(frozen=True)
class BackendCapabilities:
    name: str
    # A provider that forgets this flag gets billing protection by default.
    enforce_expiry: bool = True
    # True when the plugin's expires_at row is the effective lifetime control.
    # Modal's provider timeout is fixed at creation, so it stays false there.
    lifetime_extension_supported: bool = False
    # True when a provider-bundled machine SKU must be selected first.
    requires_hardware_selection: bool = False
    # True when cpu/memory/gpu can be requested independently.
    configurable_resources: bool = True


@runtime_checkable
class SandboxDriver(Protocol):
    """Small provider contract: catalog and lifecycle."""

    capabilities: BackendCapabilities

    def capabilities_for(self, *, provider: str | None = None) -> BackendCapabilities:
        """Capabilities of the backend that would serve ``provider``.

        Keyed by data value so services never branch on provider names; a
        single-provider backend ignores the argument and returns its own.
        """
        ...

    def acquire(
        self,
        *,
        request: SandboxRequest,
        on_phase: OnPhase | None = None,
        on_created: OnCreated | None = None,
    ) -> ProvisionedSandbox: ...

    def is_alive(self, *, sandbox_id: str) -> bool: ...

    def terminate(self, *, sandbox_id: str) -> bool: ...

    def refresh_ssh_endpoint(self, *, sandbox_id: str) -> tuple[str, int] | None:
        """Refresh a live endpoint; None means unsupported or no live endpoint."""
        ...

    def hardware_catalog(
        self, *, gpu: str | None = None, region: str | None = None
    ) -> dict[str, Any] | None:
        """Return requestable hardware, or None when no catalog is available."""
        ...


@runtime_checkable
class SandboxBackend(SandboxDriver, Protocol):
    """Provider-neutral lifecycle and operational-channel contract."""

    def read_transcript(
        self,
        *,
        sandbox_id: str,
        experiment_id: str,
        volume_name: str,
        workdir: str,
        tail: int | None = None,
        ssh_host: str = "",
        ssh_port: int = 0,
        ssh_user: str = "",
        key_path: str = "",
    ) -> TranscriptTail: ...

    def sample_metrics(
        self,
        *,
        sandbox_id: str,
        ssh_host: str = "",
        ssh_port: int = 0,
        ssh_user: str = "",
        key_path: str = "",
    ) -> dict[str, Any] | None: ...

    def read_runs(
        self,
        *,
        sandbox_id: str,
        workdir: str,
        ssh_host: str = "",
        ssh_port: int = 0,
        ssh_user: str = "",
        key_path: str = "",
    ) -> list[dict[str, Any]] | None: ...

    def write_secrets(
        self,
        *,
        sandbox_id: str,
        secrets: Mapping[str, str],
        ssh_host: str = "",
        ssh_port: int = 0,
        key_path: str = "",
    ) -> bool: ...

    def sandbox_environment(self) -> dict: ...

    def health(self) -> dict: ...

    def find_sandbox_id(
        self, *, experiment_id: str, sandbox_uid: str = "", provider: str = ""
    ) -> str | None:
        """Find an orphan within the row's provider.

        ``None`` is authoritative absence; provider failure must raise.
        """
        ...

    def qualified_sandbox_id(self, *, sandbox_id: str, provider: str = "") -> str:
        """Qualify by recorded owner; raise rather than route to another provider."""
        ...

    def sandbox_secrets(self, *, hf_token: str = "") -> dict[str, str]:
        """Return post-boot secrets for this provisioning user."""
        ...

    def shutdown(self) -> None:
        """Optionally release backend-level resources. Unsupported backends no-op."""
        ...


class SandboxBackendBase:
    capabilities: BackendCapabilities

    @staticmethod
    def _notify(callback: Callable[..., None] | None, *args: Any) -> None:
        if callback is not None:
            callback(*args)

    def _probe_health(self, probe: Callable[[], Any]) -> dict[str, Any]:
        try:
            probe()
            return {"ok": True, "backend": self.capabilities.name}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "backend": self.capabilities.name, "error": str(exc)}

    def capabilities_for(self, *, provider: str | None = None) -> BackendCapabilities:
        _ = provider
        return self.capabilities

    def sandbox_environment(self) -> dict[str, Any]:
        available_tokens = ["HF_TOKEN"] if os.environ.get("HF_TOKEN") else []
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

    def sample_metrics(
        self,
        *,
        sandbox_id: str,
        ssh_host: str = "",
        ssh_port: int = 0,
        ssh_user: str = "",
        key_path: str = "",
    ) -> dict[str, Any] | None:
        return None

    def read_runs(
        self,
        *,
        sandbox_id: str,
        workdir: str,
        ssh_host: str = "",
        ssh_port: int = 0,
        ssh_user: str = "",
        key_path: str = "",
    ) -> list[dict[str, Any]] | None:
        return None

    def refresh_ssh_endpoint(self, *, sandbox_id: str) -> tuple[str, int] | None:
        return None

    def _selection_catalog(
        self, *, reason: str, options: list[dict[str, Any]],
        regions: list[str] | None = None,
    ) -> dict[str, Any]:
        if regions is None:
            regions = sorted({r for option in options for r in option.get("regions", [])})
        return {
            "provider": self.capabilities.name,
            "selection_required": self.capabilities.requires_hardware_selection,
            "select_with": "instance_type",
            "reason": reason,
            "regions": regions,
            "count": len(options),
            "options": options,
        }

    def hardware_catalog(
        self, *, gpu: str | None = None, region: str | None = None
    ) -> dict[str, Any] | None:
        return None

    def find_sandbox_id(
        self, *, experiment_id: str, sandbox_uid: str = "", provider: str = ""
    ) -> str | None:
        _ = provider  # single-provider backend: it owns every row it serves
        return None

    def qualified_sandbox_id(self, *, sandbox_id: str, provider: str = "") -> str:
        _ = provider
        return sandbox_id

    def sandbox_secrets(self, *, hf_token: str = "") -> dict[str, str]:
        _ = hf_token
        return {}

    def write_secrets(
        self,
        *,
        sandbox_id: str,
        secrets: Mapping[str, str],
        ssh_host: str = "",
        ssh_port: int = 0,
        key_path: str = "",
    ) -> bool:
        return False

    def shutdown(self) -> None:
        return None


def qualified_row_sandbox_id(
    *, backend: SandboxBackend, row: Mapping[str, Any]
) -> str:
    """Qualify legacy native IDs with the durable row's provider.

    Using today's default could read, terminate, or disclose secrets to the
    wrong provider.
    """
    sandbox_id = str(row.get("sandbox_id") or "")
    if not sandbox_id:
        return ""
    return str(
        backend.qualified_sandbox_id(
            sandbox_id=sandbox_id,
            provider=str(row.get("provider") or ""),
        )
    )


__all__ = [
    "BackendCapabilities",
    "BackendPermissionError",
    "BackendUnavailableError",
    "BackendValidationError",
    "CapacityUnavailableError",
    "ExecutionBackendError",
    "OnCreated",
    "OnPhase",
    "ProvisionedSandbox",
    "SANDBOX_STATES",
    "SandboxBackend",
    "SandboxBackendBase",
    "SandboxDriver",
    "SandboxRequest",
    "TranscriptTail",
    "qualified_row_sandbox_id",
]

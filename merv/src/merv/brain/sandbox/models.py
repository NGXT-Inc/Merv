# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Provider-neutral Sandbox values, protocol, and errors."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import os
import secrets
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

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


@dataclass(frozen=True, slots=True)
class SandboxTarget:
    """Everything an adapter needs to address an existing sandbox."""

    sandbox_id: str
    sandbox_uid: str = ""
    provider: str = ""
    experiment_id: str = ""
    volume_name: str = ""
    workdir: str = ""
    ssh_host: str = ""
    ssh_port: int = 0
    ssh_user: str = ""
    key_path: str = ""

    @classmethod
    def from_row(
        cls, row: Mapping[str, Any], *, key_path: str = ""
    ) -> "SandboxTarget":
        return cls(
            sandbox_id=str(row.get("sandbox_id") or ""),
            sandbox_uid=str(row.get("sandbox_uid") or ""),
            provider=str(row.get("provider") or ""),
            experiment_id=str(row.get("experiment_id") or ""),
            volume_name=str(row.get("volume_name") or ""),
            workdir=str(row.get("workdir") or ""),
            ssh_host=str(row.get("ssh_host") or ""),
            ssh_port=int(row.get("ssh_port") or 0),
            ssh_user=str(row.get("ssh_user") or ""),
            key_path=key_path,
        )

    def addressed(self, backend: "SandboxBackend") -> "SandboxTarget":
        """Return this target with a provider-qualified resource ID."""
        return replace(
            self,
            sandbox_id=backend.qualified_sandbox_id(
                sandbox_id=self.sandbox_id,
                provider=self.provider,
            ),
        )

    def for_experiment(self, experiment_id: str) -> "SandboxTarget":
        return replace(self, experiment_id=experiment_id)


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


class ProviderAdmission(Protocol):
    """Request-time project gate: raise to block procurement on a provider
    (the Sandboxes → Configure disable switch); return to admit."""

    def __call__(self, *, project_id: str, provider: str) -> None: ...


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
        target: SandboxTarget,
        tail: int | None = None,
    ) -> TranscriptTail: ...

    def sample_metrics(
        self,
        *,
        target: SandboxTarget,
    ) -> dict[str, Any] | None: ...

    def read_runs(
        self,
        *,
        target: SandboxTarget,
    ) -> list[dict[str, Any]] | None: ...

    def write_secrets(
        self,
        *,
        target: SandboxTarget,
        secrets: Mapping[str, str],
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
        target: SandboxTarget,
    ) -> dict[str, Any] | None:
        return None

    def read_runs(
        self,
        *,
        target: SandboxTarget,
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
        target: SandboxTarget,
        secrets: Mapping[str, str],
    ) -> bool:
        return False

    def shutdown(self) -> None:
        return None


class DisabledSandboxBackend(SandboxBackendBase):
    """Fail-closed backend used when the Sandbox feature is switched off."""

    capabilities = BackendCapabilities(name="disabled", enforce_expiry=False)

    @staticmethod
    def _disabled() -> BackendUnavailableError:
        return BackendUnavailableError("Sandbox is disabled in Merv settings")

    def acquire(
        self,
        *,
        request: SandboxRequest,
        on_phase: OnPhase | None = None,
        on_created: OnCreated | None = None,
    ) -> ProvisionedSandbox:
        raise self._disabled()

    def is_alive(self, *, sandbox_id: str) -> bool:
        raise self._disabled()

    def terminate(self, *, sandbox_id: str) -> bool:
        raise self._disabled()

    def read_transcript(
        self,
        *,
        target: SandboxTarget,
        tail: int | None = None,
    ) -> TranscriptTail:
        raise self._disabled()

    def health(self) -> dict[str, Any]:
        return {
            "ok": False,
            "backend": "disabled",
            "disabled": True,
            "error": "Sandbox is disabled in Merv settings",
        }


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


# Lifecycle values and cleanup fences

ACTIVE_SANDBOX_STATUSES: frozenset[str] = frozenset({"running"})
# Unconfirmed destruction remains visible and retryable, never terminal.
CLEANUP_PENDING_STATUS = "cleanup_pending"
TERMINAL_SANDBOX_STATUSES: frozenset[str] = frozenset({"terminated", "failed"})
UNREUSABLE_SANDBOX_STATUSES: frozenset[str] = TERMINAL_SANDBOX_STATUSES | {
    CLEANUP_PENDING_STATUS
}
# Stay below the common ~60s MCP timeout.
DEFAULT_REQUEST_WAIT_SECONDS = 45.0
# Server cap for callers whose transport allows a long receipt poll.
RUNS_WAIT_CAP_SECONDS = 300.0
RUNS_WAIT_POLL_SECONDS = 5.0
# Reconcile old provisioning rows whose local job vanished.
DEFAULT_STALE_PROVISION_SECONDS = 15 * 60.0
# Lambda cold boots make tighter polling wasteful.
POLL_AFTER_SECONDS = 30
METRICS_CACHE_TTL_SECONDS = 2.0
# VM providers such as Lambda lack Modal's server-side lifetime enforcement.
DEFAULT_REAPER_INTERVAL_SECONDS = 30.0
DEFAULT_SANDBOX_IDLE_SECONDS = 3600.0
# The final backoff repeats forever while a VM may still bill.
CLEANUP_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (60.0, 300.0, 900.0, 3600.0)
# Longer than bounded provider termination; a replacement token fences late writes.
CLEANUP_INFLIGHT_DEADLINE_SECONDS = 600.0
_CLEANUP_ATTEMPT_PREFIX = "cleanup_attempt_"
_CLEANUP_INFLIGHT_PREFIX = "cleanup_inflight_"


def cleanup_attempt_phase(*, attempts: int) -> str:
    """Store retry count in the existing phase column while nobody holds it."""
    return f"{_CLEANUP_ATTEMPT_PREFIX}{max(int(attempts), 1)}"


def new_cleanup_token() -> str:
    return secrets.token_hex(8)


def cleanup_inflight_phase(*, attempts: int, token: str) -> str:
    """Encode ownership so reclaimed workers fail their completion CAS."""
    return f"{_CLEANUP_INFLIGHT_PREFIX}{max(int(attempts), 1)}:{token}"


def cleanup_attempts(*, phase: Any) -> int:
    """Read retry count from parked or in-flight markers."""
    text = str(phase or "")
    for prefix in (_CLEANUP_ATTEMPT_PREFIX, _CLEANUP_INFLIGHT_PREFIX):
        if not text.startswith(prefix):
            continue
        try:
            return max(int(text[len(prefix):].split(":", 1)[0]), 0)
        except ValueError:
            return 0
    return 0


def cleanup_inflight_token(*, phase: Any) -> str:
    text = str(phase or "")
    if not text.startswith(_CLEANUP_INFLIGHT_PREFIX):
        return ""
    return text.partition(":")[2]


def public_phase(*, phase: Any) -> str:
    """A phase safe to project: the in-flight ownership token never leaves the row."""
    text = str(phase or "")
    if not cleanup_inflight_token(phase=text):
        return text
    return cleanup_attempt_phase(attempts=cleanup_attempts(phase=text))


def cleanup_claim_expired(*, claimed_at: datetime | None, now: datetime) -> bool:
    """Treat an unstamped claim as reclaimable because freshness is unproven."""
    if claimed_at is None:
        return True
    return claimed_at <= cleanup_claim_cutoff(now=now)


def cleanup_claim_cutoff(*, now: datetime) -> datetime:
    """Return the same reclaim boundary used by both reader and database CAS."""
    return now - timedelta(seconds=CLEANUP_INFLIGHT_DEADLINE_SECONDS)


@dataclass(frozen=True, slots=True)
class CleanupClaim:
    """Cleanup ownership plus the exact phase fence completion must assert."""

    granted: bool
    token: str = ""
    attempts: int = 0
    phase: str = ""

    def __bool__(self) -> bool:
        return self.granted


CLEANUP_CLAIM_REFUSED = CleanupClaim(granted=False)
CLEANUP_CLAIM_UNFENCED = CleanupClaim(granted=True)


def cleanup_retry_due(
    *, attempts: int, last_attempt_at: datetime | None, now: datetime
) -> bool:
    if last_attempt_at is None:
        return True
    index = min(max(attempts, 1), len(CLEANUP_RETRY_BACKOFF_SECONDS)) - 1
    return (
        now - last_attempt_at
    ).total_seconds() >= CLEANUP_RETRY_BACKOFF_SECONDS[index]

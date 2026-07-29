# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Lambda Labs Sandbox adapter."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from ...kernel.env import env_value
from ..remote.bootstrap_tools import BASELINE_APT_PACKAGES, ML_PYTHON_PACKAGES
from ..remote.vm_bootstrap import build_standard_user_data
from ..sandbox_paths import DEFAULT_DATA_DIR, DEFAULT_REMOTE_ROOT
from .base import (
    BackendCapabilities,
    BackendUnavailableError,
    BackendValidationError,
    CapacityUnavailableError,
    OnCreated,
    OnPhase,
    ProvisionedSandbox,
    SandboxRequest,
    SshInputRunner,
    SshRunner,
    VmSshSandboxBackend,
    _absolute_posix_path,
    _env_discovery_disabled,
    _first_env,
    _float_or_none,
    _http_base_url,
    _int_or_zero,
    _load_env_text,
    _norm,
    _positive_float,
    _positive_int,
    _required_env,
    _validate_data_dir,
    _vm_name as _sandbox_name,
    bearer_json_headers,
    price_sort_key,
    request_json,
)


# Configuration

DEFAULT_BASE_URL = "https://cloud.lambda.ai/api/v1"
DEFAULT_SANDBOX_DATA_DIR = DEFAULT_DATA_DIR
DEFAULT_SSH_USER = "ubuntu"
DEFAULT_INSTANCE_POLL_TIMEOUT_SECONDS = 900
DEFAULT_INSTANCE_POLL_INTERVAL_SECONDS = 10.0


@dataclass(frozen=True)
class LambdaCloudConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls) -> "LambdaCloudConfig":
        load_lambda_env_file()
        return cls(
            api_key=_required_env(
                "MERV_LAMBDA_API_KEY",
                "LAMBDA_LABS_API_KEY",
                "LAMBDA_API_KEY",
                error="Lambda Cloud API key is required; set MERV_LAMBDA_API_KEY, "
                "LAMBDA_LABS_API_KEY, or LAMBDA_API_KEY",
            ),
            base_url=_http_base_url("MERV_LAMBDA_API_BASE", DEFAULT_BASE_URL),
        )


@dataclass(frozen=True)
class LambdaSandboxConfig:
    cloud: LambdaCloudConfig
    # Region + instance type are *optional fallback defaults*. The agent chooses
    # the machine per request (sandbox.request instance_type/region); these env
    # values only fill in when a request omits them. Empty means "let the agent
    # pick from live availability" — sandbox.request returns a selection menu.
    region_name: str = ""
    instance_type_name: str = ""
    ssh_user: str = DEFAULT_SSH_USER
    # Remote root under which each experiment's one synced folder
    # (`<root>/<experiment_id>`) is created.
    remote_root: str = DEFAULT_REMOTE_ROOT
    sandbox_data_dir: str = DEFAULT_SANDBOX_DATA_DIR
    poll_timeout_seconds: int = DEFAULT_INSTANCE_POLL_TIMEOUT_SECONDS
    poll_interval_seconds: float = DEFAULT_INSTANCE_POLL_INTERVAL_SECONDS

    @classmethod
    def from_env(cls) -> "LambdaSandboxConfig":
        cloud = LambdaCloudConfig.from_env()
        region_name = _first_env(
            "MERV_LAMBDA_REGION",
            "LAMBDA_LABS_REGION",
            "LAMBDA_REGION",
        )
        instance_type_name = _first_env(
            "MERV_LAMBDA_INSTANCE_TYPE",
            "LAMBDA_LABS_INSTANCE_TYPE",
            "LAMBDA_INSTANCE_TYPE",
        )
        remote_root = _absolute_posix_path(
            env_value("MERV_LAMBDA_WORKDIR") or DEFAULT_REMOTE_ROOT,
            field="MERV_LAMBDA_WORKDIR",
        )
        sandbox_data_dir = _absolute_posix_path(
            env_value("MERV_LAMBDA_DATA_DIR") or DEFAULT_SANDBOX_DATA_DIR,
            field="MERV_LAMBDA_DATA_DIR",
        )
        _validate_data_dir(
            sandbox_data_dir, remote_root=remote_root, field="MERV_LAMBDA_DATA_DIR"
        )
        return cls(
            cloud=cloud,
            region_name=region_name,
            instance_type_name=instance_type_name,
            ssh_user=env_value("MERV_LAMBDA_SSH_USER") or DEFAULT_SSH_USER,
            remote_root=remote_root,
            sandbox_data_dir=sandbox_data_dir,
            poll_timeout_seconds=_positive_int(
                env_value("MERV_LAMBDA_POLL_TIMEOUT")
                or DEFAULT_INSTANCE_POLL_TIMEOUT_SECONDS,
                field="MERV_LAMBDA_POLL_TIMEOUT",
            ),
            poll_interval_seconds=_positive_float(
                env_value("MERV_LAMBDA_POLL_INTERVAL")
                or DEFAULT_INSTANCE_POLL_INTERVAL_SECONDS,
                field="MERV_LAMBDA_POLL_INTERVAL",
            ),
        )


def load_lambda_env_file() -> None:
    """Load Lambda credentials/settings from the configured plugin env file.

    Only an EXPLICITLY configured env file is ever read (no implicit package-root
    ``.env`` fallback), so this is already the secret-store seam: in control mode
    point ``MERV_LAMBDA_ENV_FILE`` at a mounted secret. Control mode
    additionally refuses to fall through to the Modal env-file alias so a user
    machine's ``MERV_MODAL_ENV_FILE`` can't smuggle creds into the
    cloud — the control plane reads its own env / secret store only.
    """

    configured = env_value("MERV_LAMBDA_ENV_FILE")
    if not configured and not _env_discovery_disabled():
        configured = env_value("MERV_MODAL_ENV_FILE")
    if not configured:
        return
    path = Path(configured).expanduser()
    if not path.exists():
        raise BackendValidationError(f"Lambda env file does not exist: {path}")
    _load_env_text(path.read_text())


# Hardware catalog

def summarize_instance_types(
    raw_types: dict[str, Any],
    *,
    region: str | None = None,
    gpu: str | None = None,
    instance_type: str | None = None,
    min_gpus: int | None = None,
    only_available: bool = True,
) -> dict[str, Any]:
    """Filter + normalize Lambda instance types into the rich catalog shape."""

    region_filter = _norm(region)
    gpu_filter = _norm(gpu)
    instance_type_filter = _norm(instance_type)
    min_gpu_count = int(min_gpus) if min_gpus is not None else None

    entries: list[dict[str, Any]] = []
    for name, item in sorted(raw_types.items()):
        if not isinstance(item, dict):
            continue
        instance = item.get("instance_type") or {}
        if not isinstance(instance, dict):
            continue
        entry_name = str(instance.get("name") or name)
        specs = instance.get("specs") if isinstance(instance.get("specs"), dict) else {}
        regions = [
            {
                "name": str(region_item.get("name") or ""),
                "description": str(region_item.get("description") or ""),
            }
            for region_item in item.get("regions_with_capacity_available", [])
            if isinstance(region_item, dict) and region_item.get("name")
        ]
        region_names = {str(item["name"]).lower() for item in regions}
        gpu_description = str(instance.get("gpu_description") or "")
        gpus = _int_or_zero(specs.get("gpus"))
        available = bool(regions)

        if only_available and not available:
            continue
        if region_filter and region_filter not in region_names:
            continue
        if gpu_filter and gpu_filter not in _norm(gpu_description) and gpu_filter not in _norm(entry_name):
            continue
        if instance_type_filter and instance_type_filter != _norm(entry_name):
            continue
        if min_gpu_count is not None and gpus < min_gpu_count:
            continue

        # Tri-state on purpose: a SKU Lambda quotes no price for stays
        # unpriced, so a spend policy can refuse it instead of billing $0/hr.
        price_cents = _float_or_none(instance.get("price_cents_per_hour"))
        entries.append(
            {
                "name": entry_name,
                "description": str(instance.get("description") or ""),
                "gpu_description": gpu_description,
                "price_cents_per_hour": None if price_cents is None else int(price_cents),
                "price_usd_per_hour": None if price_cents is None else price_cents / 100.0,
                "specs": {
                    "vcpus": _int_or_zero(specs.get("vcpus")),
                    "memory_gib": _int_or_zero(specs.get("memory_gib")),
                    "storage_gib": _int_or_zero(specs.get("storage_gib")),
                    "gpus": gpus,
                },
                "regions_with_capacity_available": regions,
                "available": available,
            }
        )

    all_regions = sorted(
        {
            region_item["name"]
            for entry in entries
            for region_item in entry["regions_with_capacity_available"]
        }
    )
    return {
        "provider": "lambda_labs",
        "count": len(entries),
        "regions": all_regions,
        "instance_types": entries,
    }


def to_agent_options(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the rich catalog into a compact menu the agent chooses from.

    Sorted cheapest-first so the agent (and the user reading the menu) sees the
    least expensive viable machine at the top.
    """

    options: list[dict[str, Any]] = []
    for entry in summary.get("instance_types", []):
        specs = entry.get("specs") or {}
        options.append(
            {
                "instance_type": entry.get("name"),
                "gpu": _gpu_label(entry.get("gpu_description") or "", entry.get("name") or ""),
                "gpu_description": entry.get("gpu_description") or "",
                "gpu_count": _int_or_zero(specs.get("gpus")),
                "vcpus": _int_or_zero(specs.get("vcpus")),
                "memory_gib": _int_or_zero(specs.get("memory_gib")),
                "storage_gib": _int_or_zero(specs.get("storage_gib")),
                "price_usd_per_hour": entry.get("price_usd_per_hour"),
                "regions": [r["name"] for r in entry.get("regions_with_capacity_available", [])],
                "available": bool(entry.get("available")),
            }
        )
    options.sort(key=price_sort_key)
    return options


def find_option(summary: dict[str, Any], *, instance_type: str) -> dict[str, Any] | None:
    """Return the flat menu entry for one instance type, or None if absent."""
    wanted = _norm(instance_type)
    for option in to_agent_options(summary):
        if _norm(option.get("instance_type")) == wanted:
            return option
    return None


def _gpu_label(gpu_description: str, name: str) -> str:
    """Best-effort short GPU label, e.g. 'H100' from 'H100 (80 GB SXM5)'."""
    text = gpu_description.strip()
    if text:
        return text.split("(")[0].strip() or text
    # Fall back to the SKU name's GPU token, e.g. gpu_1x_h100_sxm5 -> H100.
    parts = [p for p in name.lower().split("_") if p]
    for part in parts:
        if any(ch.isdigit() for ch in part) and any(ch.isalpha() for ch in part):
            return part.upper()
    return ""


# Provider API client

class LambdaCloudClient:
    def __init__(self, *, config: LambdaCloudConfig | None = None, timeout: float = 30.0) -> None:
        self.config = config or LambdaCloudConfig.from_env()
        self.timeout = timeout

    def list_instance_types(self) -> dict[str, Any]:
        data = self._request("GET", "/instance-types")
        raw = data.get("data")
        if not isinstance(raw, dict):
            raise BackendUnavailableError("Lambda Cloud returned malformed instance-types data")
        return raw

    def list_instances(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/instances")
        raw = data.get("data")
        if not isinstance(raw, list):
            raise BackendUnavailableError("Lambda Cloud returned malformed instances data")
        return [item for item in raw if isinstance(item, dict)]

    def get_instance(self, instance_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/instances/{instance_id}")
        raw = data.get("data")
        if not isinstance(raw, dict):
            raise BackendUnavailableError("Lambda Cloud returned malformed instance data")
        return raw

    def add_ssh_key(self, *, name: str, public_key: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/ssh-keys",
            body={"name": name, "public_key": public_key},
        )
        raw = data.get("data")
        if not isinstance(raw, dict):
            raise BackendUnavailableError("Lambda Cloud returned malformed SSH key data")
        return raw

    def list_ssh_keys(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/ssh-keys")
        raw = data.get("data")
        if not isinstance(raw, list):
            raise BackendUnavailableError("Lambda Cloud returned malformed SSH keys data")
        return [item for item in raw if isinstance(item, dict)]

    def delete_ssh_key(self, key_id: str) -> None:
        self._request("DELETE", f"/ssh-keys/{key_id}")

    def launch_instance(
        self,
        *,
        region_name: str,
        instance_type_name: str,
        ssh_key_name: str,
        name: str,
        user_data: str,
    ) -> str:
        data = self._request(
            "POST",
            "/instance-operations/launch",
            body={
                "region_name": region_name,
                "instance_type_name": instance_type_name,
                "ssh_key_names": [ssh_key_name],
                "file_system_names": [],
                "quantity": 1,
                "name": name,
                "hostname": name,
                "user_data": user_data,
            },
        )
        raw = data.get("data")
        if not isinstance(raw, dict):
            raise BackendUnavailableError("Lambda Cloud returned malformed launch data")
        ids = raw.get("instance_ids")
        if not isinstance(ids, list) or not ids or not isinstance(ids[0], str):
            raise BackendUnavailableError("Lambda Cloud launch returned no instance id")
        return ids[0]

    def terminate_instances(self, instance_ids: list[str]) -> list[dict[str, Any]]:
        data = self._request(
            "POST",
            "/instance-operations/terminate",
            body={"instance_ids": instance_ids},
        )
        raw = data.get("data")
        if not isinstance(raw, dict):
            raise BackendUnavailableError("Lambda Cloud returned malformed terminate data")
        terminated = raw.get("terminated_instances")
        if not isinstance(terminated, list):
            raise BackendUnavailableError("Lambda Cloud returned malformed terminated instances data")
        return [item for item in terminated if isinstance(item, dict)]

    def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return request_json(
            provider="Lambda Cloud",
            method=method,
            base_url=self.config.base_url,
            path=path,
            body=body,
            headers=bearer_json_headers(self.config.api_key, "merv/0.0005"),
            timeout=self.timeout,
            require_object=True,
        )


# Sandbox adapter

ACTIVE_INSTANCE_STATUSES = frozenset({"active"})
LIVE_INSTANCE_STATUSES = frozenset({"booting", "active", "unhealthy"})
TERMINAL_INSTANCE_STATUSES = frozenset(
    {"terminated", "terminating", "preempted"}
)


LAMBDA_APT_PACKAGES: tuple[str, ...] = (
    "openssh-server",
    "ca-certificates",
    *BASELINE_APT_PACKAGES,
)

class LambdaLabsSandboxBackend(VmSshSandboxBackend):
    resource_label = "Lambda instance"
    live_statuses = LIVE_INSTANCE_STATUSES
    ready_statuses = ACTIVE_INSTANCE_STATUSES
    terminal_statuses = TERMINAL_INSTANCE_STATUSES
    # Lambda bundles GPU, CPU, and RAM, so requests choose a live machine SKU.
    capabilities = BackendCapabilities(
        name="lambda_labs",
        lifetime_extension_supported=True,
        requires_hardware_selection=True,
        configurable_resources=False,
    )

    def __init__(
        self,
        *,
        config: LambdaSandboxConfig | None = None,
        client: LambdaCloudClient | None = None,
        ssh_runner: SshRunner | None = None,
        ssh_input_runner: SshInputRunner | None = None,
    ) -> None:
        super().__init__(
            ssh_runner=ssh_runner,
            ssh_input_runner=ssh_input_runner,
        )
        # Missing credentials surface at call time instead of breaking startup.
        self._config = config
        self._client = client

    @property
    def config(self) -> LambdaSandboxConfig:
        return self._lazy_provider_config(LambdaSandboxConfig.from_env)

    @property
    def client(self) -> LambdaCloudClient:
        return self._lazy_provider_client(LambdaCloudClient)

    def acquire(
        self,
        *,
        request: SandboxRequest,
        on_phase: OnPhase | None = None,
        on_created: OnCreated | None = None,
    ) -> ProvisionedSandbox:
        instance_name = _sandbox_name(request.sandbox_uid or request.experiment_id)
        key_name = f"{instance_name}-key"
        instance_type = (request.instance_type or self.config.instance_type_name or "").strip()
        if not instance_type:
            raise BackendValidationError(
                "Lambda Labs requires an instance_type (it bundles GPU + CPU + RAM "
                "into one machine). Call sandbox.options, or sandbox.request without "
                "an instance_type, to see live availability, then pick a SKU."
            )
        # A request-selected SKU must not inherit the default SKU's region.
        default_region = "" if request.instance_type else self.config.region_name
        self._notify(on_phase, "checking_capacity", instance_type)
        region, specs = self._resolve_placement(
            instance_type=instance_type,
            region=(request.region or default_region or "").strip(),
            requested_gpu=request.gpu,
        )

        self._notify(on_phase, "registering_ssh_key", key_name)
        key_id = ""
        instance_id = ""
        try:
            key = self.client.add_ssh_key(name=key_name, public_key=request.public_key)
            key_id = str(key.get("id") or "")

            self._notify(on_phase, "creating", f"{instance_type} in {region}")
            workdir = self._sandbox_workdir(request)
            # Credentials arrive post-boot, never in provider user_data.
            user_data = self._standard_user_data(
                request=request,
                workdir=workdir,
                apt_packages=LAMBDA_APT_PACKAGES,
            )
            instance_id = self.client.launch_instance(
                region_name=region,
                instance_type_name=instance_type,
                ssh_key_name=key_name,
                name=instance_name,
                user_data=user_data,
            )
            self._notify(on_created, instance_id, instance_name)

            self._notify(on_phase, "connecting", "waiting for active instance and ssh")
            instance = self._wait_for_vm(sandbox_id=instance_id)
            ip = str(instance.get("ip") or instance.get("hostname") or "")
            if not ip:
                raise BackendUnavailableError("Lambda instance became active without a public IP")
            self._wait_for_ssh(host=ip)
            return ProvisionedSandbox(
                sandbox_id=instance_id,
                ssh_host=ip,
                ssh_port=22,
                **self._provisioned_vm_fields(workdir=workdir),
                gpu=str(specs.get("gpu") or request.gpu or ""),
                cpu=float(specs["vcpus"]) if specs.get("vcpus") else None,
                memory=int(specs["memory_gib"]) * 1024 if specs.get("memory_gib") else None,
                instance_type=instance_type,
                region=region,
                price_usd_per_hour=float(specs.get("price_usd_per_hour") or 0.0),
            )
        except Exception:
            if instance_id:
                with suppress(Exception):
                    self.client.terminate_instances([instance_id])
            if key_id:
                with suppress(Exception):
                    self.client.delete_ssh_key(key_id)
            raise

    def _get_resource(self, sandbox_id: str) -> dict[str, Any]:
        return self.client.get_instance(sandbox_id)

    def _resource_is_addressable(self, resource: Mapping[str, Any]) -> bool:
        return bool(
            resource.get("ip") or resource.get("hostname")
        )

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

    def health(self) -> dict:
        return self._probe_health(lambda: self.client.list_instance_types())

    def find_sandbox_id(
        self, *, experiment_id: str, sandbox_uid: str = "", provider: str = ""
    ) -> str | None:
        return self._find_named_resource_id(
            name=_sandbox_name(sandbox_uid or experiment_id),
            resources=self.client.list_instances(),
        )

    def hardware_catalog(
        self, *, gpu: str | None = None, region: str | None = None
    ) -> dict[str, Any]:
        """Cheapest-first machine SKUs with current capacity."""
        summary = summarize_instance_types(
            self.client.list_instance_types(),
            gpu=gpu,
            region=region,
            only_available=True,
        )
        options = to_agent_options(summary)
        return self._selection_catalog(
            reason=(
                "Lambda Labs bundles GPU, CPU, and RAM into fixed machine types; "
                "pick one instance_type rather than cpu/memory."
            ),
            regions=summary["regions"],
            options=options,
        )

    def _resolve_placement(
        self, *, instance_type: str, region: str, requested_gpu: str | None
    ) -> tuple[str, dict[str, Any]]:
        """Validate capacity; otherwise choose the first available region."""
        instance_types = self.client.list_instance_types()
        row = instance_types.get(instance_type)
        if not isinstance(row, dict):
            offered = ", ".join(sorted(instance_types)) or "(none)"
            raise BackendValidationError(
                f"Lambda instance type is not currently offered: {instance_type}. "
                f"Currently offered: {offered}."
            )
        instance = row.get("instance_type")
        if not isinstance(instance, dict):
            raise BackendUnavailableError("Lambda Cloud returned malformed instance type data")
        if requested_gpu:
            gpu_text = " ".join(
                str(instance.get(key) or "")
                for key in ("name", "description", "gpu_description")
            ).upper()
            if requested_gpu.upper() not in gpu_text:
                raise BackendValidationError(
                    f"requested gpu {requested_gpu} does not match Lambda instance "
                    f"type {instance_type} ({instance.get('gpu_description') or 'unknown GPU'})"
                )
        regions = row.get("regions_with_capacity_available")
        if not isinstance(regions, list):
            raise BackendUnavailableError("Lambda Cloud returned malformed capacity data")
        available_regions = sorted(
            str(item.get("name") or "")
            for item in regions
            if isinstance(item, dict) and item.get("name")
        )
        if region:
            if region not in available_regions:
                where = ", ".join(available_regions) or "(no regions)"
                raise CapacityUnavailableError(
                    f"Lambda instance type {instance_type} has no current capacity in "
                    f"{region}. Regions with capacity now: {where}."
                )
            chosen = region
        else:
            if not available_regions:
                raise CapacityUnavailableError(
                    f"Lambda instance type {instance_type} has no current capacity in "
                    "any region. Call sandbox.options to pick an available SKU."
                )
            chosen = available_regions[0]
        specs_raw = instance.get("specs") if isinstance(instance.get("specs"), dict) else {}
        option = find_option(
            summarize_instance_types(instance_types, only_available=False),
            instance_type=instance_type,
        ) or {}
        specs = {
            "gpu": option.get("gpu") or str(instance.get("gpu_description") or ""),
            "gpus": _int_or_zero(specs_raw.get("gpus")),
            "vcpus": _int_or_zero(specs_raw.get("vcpus")),
            "memory_gib": _int_or_zero(specs_raw.get("memory_gib")),
            "price_usd_per_hour": float(option.get("price_usd_per_hour") or 0.0),
        }
        return chosen, specs

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
                with suppress(Exception):
                    self.client.delete_ssh_key(key_id)


def build_user_data(
    *,
    public_key: str,
    experiment_id: str,
    workdir: str,
    sessions_dir: str,
    sandbox_data_dir: str,
    management_public_key: str = "",
    tokens: Mapping[str, str] | None = None,
) -> str:
    _ = tokens  # Compatibility input; credentials are delivered post-boot.
    return build_standard_user_data(
        public_key=public_key,
        experiment_id=experiment_id,
        workdir=workdir,
        sessions_dir=sessions_dir,
        sandbox_data_dir=sandbox_data_dir,
        management_public_key=management_public_key,
        apt_packages=LAMBDA_APT_PACKAGES,
        python_packages=ML_PYTHON_PACKAGES,
    )


def build_lambda_labs_sandbox_backend(*, repo_root: Path | None = None, **_kwargs: Any) -> LambdaLabsSandboxBackend:
    # Lazy: do not resolve credentials/region/instance type at construction so
    # the default backend can be built (and health-checked) with only an API key.
    return LambdaLabsSandboxBackend()

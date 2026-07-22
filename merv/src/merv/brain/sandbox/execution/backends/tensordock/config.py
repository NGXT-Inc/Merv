"""Configuration for the TensorDock v2 API and VM access."""

from __future__ import annotations

from dataclasses import dataclass

from .._config import _http_base_url, _required_env
from .....kernel.env import env_value
from ....sandbox_paths import DEFAULT_DATA_DIR, DEFAULT_REMOTE_ROOT


DEFAULT_BASE_URL = "https://dashboard.tensordock.com/api/v2"
DEFAULT_IMAGE = "ubuntu2404"
# cloud-init runs as root and the bootstrap authorizes root's key; the image
# default user varies by host, so root is the stable principal.
DEFAULT_SSH_USER = "root"
DEFAULT_INSTANCE_POLL_TIMEOUT_SECONDS = 900
DEFAULT_INSTANCE_POLL_INTERVAL_SECONDS = 10.0


@dataclass(frozen=True)
class TensorDockCloudConfig:
    token: str
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls) -> "TensorDockCloudConfig":
        return cls(
            token=_required_env(
                "MERV_TENSORDOCK_TOKEN",
                "TENSORDOCK_TOKEN",
                error="TensorDock API token is required; set "
                "MERV_TENSORDOCK_TOKEN or TENSORDOCK_TOKEN",
            ),
            base_url=_http_base_url("MERV_TENSORDOCK_API_BASE", DEFAULT_BASE_URL),
        )


@dataclass(frozen=True)
class TensorDockSandboxConfig:
    cloud: TensorDockCloudConfig
    image: str = DEFAULT_IMAGE
    ssh_user: str = DEFAULT_SSH_USER
    remote_root: str = DEFAULT_REMOTE_ROOT
    sandbox_data_dir: str = DEFAULT_DATA_DIR
    poll_timeout_seconds: int = DEFAULT_INSTANCE_POLL_TIMEOUT_SECONDS
    poll_interval_seconds: float = DEFAULT_INSTANCE_POLL_INTERVAL_SECONDS

    @classmethod
    def from_env(cls) -> "TensorDockSandboxConfig":
        return cls(
            cloud=TensorDockCloudConfig.from_env(),
            image=(env_value("MERV_TENSORDOCK_IMAGE") or DEFAULT_IMAGE).strip(),
            ssh_user=(env_value("MERV_TENSORDOCK_SSH_USER") or DEFAULT_SSH_USER).strip()
            or DEFAULT_SSH_USER,
        )

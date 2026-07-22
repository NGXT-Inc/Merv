"""Configuration for the Verda (formerly DataCrunch) API and VM access."""

from __future__ import annotations

from dataclasses import dataclass

from .._config import _first_env, _http_base_url
from .....kernel.env import env_value
from ....sandbox_backend import BackendValidationError
from ....sandbox_paths import DEFAULT_DATA_DIR, DEFAULT_REMOTE_ROOT


# Pinned to the datacrunch.io host: the verda.com rename is mid-migration and
# the API answers on both; datacrunch.io is the documented stable base today.
DEFAULT_BASE_URL = "https://api.datacrunch.io"
DEFAULT_IMAGE = "ubuntu-24.04"
DEFAULT_SSH_USER = "root"
DEFAULT_INSTANCE_POLL_TIMEOUT_SECONDS = 900
DEFAULT_INSTANCE_POLL_INTERVAL_SECONDS = 10.0


@dataclass(frozen=True)
class VerdaCloudConfig:
    client_id: str
    client_secret: str
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls) -> "VerdaCloudConfig":
        client_id = _first_env("MERV_VERDA_CLIENT_ID", "DATACRUNCH_CLIENT_ID")
        client_secret = _first_env(
            "MERV_VERDA_CLIENT_SECRET", "DATACRUNCH_CLIENT_SECRET"
        )
        if not client_id or not client_secret:
            raise BackendValidationError(
                "Verda OAuth2 credentials are required; set "
                "MERV_VERDA_CLIENT_ID and MERV_VERDA_CLIENT_SECRET "
                "(DATACRUNCH_* variants also accepted)"
            )
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            base_url=_http_base_url("MERV_VERDA_API_BASE", DEFAULT_BASE_URL),
        )


@dataclass(frozen=True)
class VerdaSandboxConfig:
    cloud: VerdaCloudConfig
    image: str = DEFAULT_IMAGE
    location_code: str = ""
    instance_type: str = ""
    ssh_user: str = DEFAULT_SSH_USER
    remote_root: str = DEFAULT_REMOTE_ROOT
    sandbox_data_dir: str = DEFAULT_DATA_DIR
    poll_timeout_seconds: int = DEFAULT_INSTANCE_POLL_TIMEOUT_SECONDS
    poll_interval_seconds: float = DEFAULT_INSTANCE_POLL_INTERVAL_SECONDS

    @classmethod
    def from_env(cls) -> "VerdaSandboxConfig":
        return cls(
            cloud=VerdaCloudConfig.from_env(),
            image=(env_value("MERV_VERDA_IMAGE") or DEFAULT_IMAGE).strip(),
            location_code=(env_value("MERV_VERDA_LOCATION") or "").strip(),
            instance_type=(env_value("MERV_VERDA_INSTANCE_TYPE") or "").strip(),
            ssh_user=(env_value("MERV_VERDA_SSH_USER") or DEFAULT_SSH_USER).strip()
            or DEFAULT_SSH_USER,
        )

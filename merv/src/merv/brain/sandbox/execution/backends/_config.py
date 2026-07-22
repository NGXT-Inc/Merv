"""Configuration helpers shared by sandbox provider backends."""

from __future__ import annotations

import os

from ....kernel.env import env_value
from ...sandbox_backend import BackendValidationError
from ...sandbox_paths import SESSIONS_DIRNAME


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

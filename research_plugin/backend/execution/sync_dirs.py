"""Compatibility re-export for the provider-neutral sync contract."""

from __future__ import annotations

from ..domain.sync_contract import (
    ARTIFACTS_TO_KEEP_DIRNAME,
    DEFAULT_DATA_DIR,
    DEFAULT_REMOTE_ROOT,
    SESSIONS_DIRNAME,
    remote_experiment_dir,
    remote_root_of,
    remote_sessions_dir,
    sync_hint,
)

__all__ = [
    "ARTIFACTS_TO_KEEP_DIRNAME",
    "DEFAULT_DATA_DIR",
    "DEFAULT_REMOTE_ROOT",
    "SESSIONS_DIRNAME",
    "remote_experiment_dir",
    "remote_root_of",
    "remote_sessions_dir",
    "sync_hint",
]

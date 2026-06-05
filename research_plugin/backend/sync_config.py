"""Configurable repo sync exclusion policy.

The Modal sync scanner consumes this small, backend-neutral policy object. The
project service owns persistence, and the scanner owns enforcement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SYNC_EXCLUSIONS_CONFIG_PATH = Path(".research_plugin") / "sync_exclusions.json"

DEFAULT_SYNC_EXCLUSIONS: dict[str, list[str]] = {
    "names": [
        ".git",
        ".research_plugin",
        ".research_plugin_job",
        ".research_plugin_sessions",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".cache",
        ".aws",
        "node_modules",
        ".DS_Store",
    ],
    "suffixes": [".pyc", ".pyo"],
    "prefixes": ["data/raw", "data/processed"],
}


@dataclass(frozen=True)
class SyncExclusionPolicy:
    """Normalized sync exclusion policy.

    names:
      Directory/file names excluded wherever they appear as a path component.
    suffixes:
      File suffixes excluded wherever they appear.
    prefixes:
      Repo-relative POSIX path prefixes excluded exactly or recursively.
    """

    names: tuple[str, ...]
    suffixes: tuple[str, ...]
    prefixes: tuple[str, ...]

    @classmethod
    def defaults(cls) -> "SyncExclusionPolicy":
        return policy_from_config(DEFAULT_SYNC_EXCLUSIONS)

    def as_config(self) -> dict[str, list[str]]:
        return {
            "names": list(self.names),
            "suffixes": list(self.suffixes),
            "prefixes": list(self.prefixes),
        }


def default_sync_exclusions() -> dict[str, list[str]]:
    """Return a deep-copy-ish default config for API/UI callers."""
    return {key: list(values) for key, values in DEFAULT_SYNC_EXCLUSIONS.items()}


def policy_from_config(config: Any | None) -> SyncExclusionPolicy:
    normalized = normalize_sync_exclusions(config)
    return SyncExclusionPolicy(
        names=tuple(normalized["names"]),
        suffixes=tuple(normalized["suffixes"]),
        prefixes=tuple(normalized["prefixes"]),
    )


def normalize_sync_exclusions(config: Any | None) -> dict[str, list[str]]:
    """Validate and normalize user-provided sync exclusion config.

    Missing keys inherit the current defaults. Empty lists are explicit and are
    preserved, which lets a user remove a default category if they want to sync
    it.
    """
    if config is None:
        return default_sync_exclusions()
    if not isinstance(config, dict):
        raise ValueError("sync_exclusions must be an object")

    result = default_sync_exclusions()
    config = dict(config)
    if "paths" in config:
        if "prefixes" in config:
            raise ValueError("sync_exclusions cannot contain both paths and prefixes")
        config["prefixes"] = config.pop("paths")

    allowed = set(result)
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise ValueError(f"unknown sync_exclusions keys: {', '.join(unknown)}")

    if "names" in config:
        result["names"] = _normalize_names(config["names"])
    if "suffixes" in config:
        result["suffixes"] = _normalize_suffixes(config["suffixes"])
    if "prefixes" in config:
        result["prefixes"] = _normalize_prefixes(config["prefixes"])
    return result


def config_from_json(raw: str | None) -> dict[str, list[str]] | None:
    if raw is None or not raw.strip():
        return None
    value = json.loads(raw)
    return normalize_sync_exclusions(value)


def config_to_json(config: Any) -> str:
    normalized = normalize_sync_exclusions(config)
    return json.dumps(normalized, sort_keys=True)


def load_sync_exclusions_file(*, repo_root: Path) -> dict[str, list[str]] | None:
    path = repo_root / SYNC_EXCLUSIONS_CONFIG_PATH
    if not path.exists():
        return None
    return normalize_sync_exclusions(json.loads(path.read_text(encoding="utf-8")))


def ensure_sync_exclusions_file(*, repo_root: Path) -> None:
    path = repo_root / SYNC_EXCLUSIONS_CONFIG_PATH
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(default_sync_exclusions(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _normalize_names(value: Any) -> list[str]:
    items = _string_list(value=value, field="names")
    result: list[str] = []
    for item in items:
        name = item.strip().strip("/")
        if not name:
            continue
        if "/" in name or "\\" in name:
            raise ValueError("sync_exclusions.names entries must be plain path names")
        result.append(name)
    return _dedupe(result)


def _normalize_suffixes(value: Any) -> list[str]:
    items = _string_list(value=value, field="suffixes")
    result: list[str] = []
    for item in items:
        suffix = item.strip()
        if not suffix:
            continue
        if "/" in suffix or "\\" in suffix:
            raise ValueError("sync_exclusions.suffixes entries must be suffix strings")
        result.append(suffix)
    return _dedupe(result)


def _normalize_prefixes(value: Any) -> list[str]:
    items = _string_list(value=value, field="prefixes")
    result: list[str] = []
    for item in items:
        prefix = item.strip().replace("\\", "/").strip("/")
        if not prefix or prefix == ".":
            continue
        result.append(prefix)
    return _dedupe(result)


def _string_list(*, value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"sync_exclusions.{field} must be a list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"sync_exclusions.{field} entries must be strings")
        result.append(item)
    return result


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

"""Local and remote scanners. Produce {path: FileFingerprint} dictionaries."""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from typing import Any

from backend.sync_config import SyncExclusionPolicy
from .types import FileFingerprint


DEFAULT_EXCLUSION_POLICY = SyncExclusionPolicy.defaults()

# Backwards-compatible names for tests/downstream imports. New code should pass
# a SyncExclusionPolicy explicitly when project config is available.
HARDCODED_EXCLUDED_NAMES: frozenset[str] = frozenset(DEFAULT_EXCLUSION_POLICY.names)
HARDCODED_EXCLUDED_SUFFIXES: tuple[str, ...] = DEFAULT_EXCLUSION_POLICY.suffixes
HARDCODED_EXCLUDED_PREFIXES: tuple[str, ...] = DEFAULT_EXCLUSION_POLICY.prefixes


def local_scan(
    *,
    repo_root: Path,
    exclusions: SyncExclusionPolicy | None = None,
) -> dict[str, FileFingerprint]:
    """Walk the local repo, stat every file, return {rel_path: fingerprint}.

    Symlinks are skipped. Excluded directory names are pruned.
    """
    repo_root = repo_root.resolve()
    policy = exclusions or DEFAULT_EXCLUSION_POLICY
    result: dict[str, FileFingerprint] = {}
    for path in _iter_local_files(repo_root, exclusions=policy):
        rel = path.relative_to(repo_root).as_posix()
        if _excluded(rel, exclusions=policy):
            continue
        stat = path.stat()
        result[rel] = FileFingerprint(
            path=rel,
            mtime_ns=int(stat.st_mtime_ns),
            size_bytes=int(stat.st_size),
        )
    return result


def remote_scan(
    *,
    volume: Any,
    repo_dir: str = "",
    exclusions: SyncExclusionPolicy | None = None,
) -> dict[str, FileFingerprint]:
    """List the modal volume recursively under repo_dir, return {rel_path: fingerprint}.

    The volume content is treated as a direct mirror of the local repo root, so
    paths returned are repo-relative (no repo_dir prefix). If repo_dir is empty
    the entire volume is treated as the repo.
    """
    prefix = repo_dir.strip("/")
    policy = exclusions or DEFAULT_EXCLUSION_POLICY
    listdir = getattr(volume, "listdir", None)
    if listdir is None:
        raise RuntimeError("modal volume object has no listdir; cannot scan remote")

    base = prefix or "/"
    raw_entries = listdir(base, recursive=True)
    entries = _collect_iter(raw_entries)

    result: dict[str, FileFingerprint] = {}
    for entry in entries:
        if not _entry_is_file(entry):
            continue
        full_path = str(getattr(entry, "path", "")).lstrip("/")
        if not full_path:
            continue
        rel = (
            full_path[len(prefix) + 1 :]
            if prefix and full_path.startswith(f"{prefix}/")
            else full_path
        )
        if not rel or _excluded(rel, exclusions=policy):
            continue
        mtime = getattr(entry, "mtime", 0) or 0
        size = int(getattr(entry, "size", 0) or 0)
        result[rel] = FileFingerprint(
            path=rel,
            mtime_ns=int(float(mtime) * 1_000_000_000),
            size_bytes=size,
        )
    return result


def _iter_local_files(repo_root: Path, *, exclusions: SyncExclusionPolicy):
    """rglob with directory pruning. Yields file Path objects only."""
    stack: list[Path] = [repo_root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            name = entry.name
            if name in exclusions.names:
                continue
            if entry.is_dir():
                stack.append(entry)
                continue
            if not entry.is_file():
                continue
            if name.endswith(exclusions.suffixes):
                continue
            yield entry


def _excluded(rel_path: str, *, exclusions: SyncExclusionPolicy) -> bool:
    parts = PurePosixPath(rel_path).parts
    if any(part in exclusions.names for part in parts):
        return True
    for prefix in exclusions.prefixes:
        if rel_path == prefix or rel_path.startswith(prefix + "/"):
            return True
    return rel_path.endswith(exclusions.suffixes)


def _entry_is_file(entry: Any) -> bool:
    kind = getattr(entry, "type", None)
    if kind is None:
        return True
    value = getattr(kind, "value", kind)
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"file", "regular"}:
            return True
        if lowered in {"directory", "dir", "symlink", "link"}:
            return False
    # Enums in modal: FileEntryType.FILE == 1, DIRECTORY == 2, SYMLINK == 3
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return True


def _collect_iter(value: Any) -> list[Any]:
    if hasattr(value, "__aiter__"):
        async def _gather() -> list[Any]:
            return [item async for item in value]

        return asyncio.run(_gather())
    return list(value)

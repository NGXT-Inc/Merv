"""Local file observation for repo resources.

The control plane records resource facts; the data plane resolves repo paths
and hashes bytes. This module is the local implementation used by local mode
and the daemon before submitting an observation to control.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from ..utils import NotFoundError, ValidationError


class LocalResourceObserver:
    """Observe one repo file without writing resource records."""

    def __init__(self, *, repo_root: Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    def observe_file(
        self,
        *,
        path: str,
        kind: str = "other",
        title: str = "",
        created_by: str = "codex",
    ) -> dict[str, Any]:
        rel_path, file_path = self.resolve_repo_file(path=path)
        stat = file_path.stat()
        return {
            "path": rel_path,
            "kind": kind,
            "title": title,
            "created_by": created_by,
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
            "size_bytes": stat.st_size,
            "content_sha256": content_sha256(file_path),
            "content_type": mimetypes.guess_type(rel_path)[0]
            or "application/octet-stream",
        }

    def resolve_repo_file(self, *, path: str) -> tuple[str, Path]:
        rel_path = repo_relative_resource_path(path=path)
        rel = Path(rel_path)
        full = (self.repo_root / rel).resolve()
        try:
            full.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValidationError("resource path escapes repo root") from exc
        if not full.exists():
            raise NotFoundError(f"resource file does not exist: {path}")
        if not full.is_file():
            raise ValidationError("v0.0001 resources must be files")
        return rel.as_posix(), full


def repo_relative_resource_path(*, path: str) -> str:
    if not path:
        raise ValidationError("path is required")
    rel = Path(path)
    if rel.is_absolute():
        raise ValidationError("resource paths must be repo-relative")
    if any(part == ".." for part in rel.parts):
        raise ValidationError("resource path may not contain '..'")
    if rel.parts and rel.parts[0] == ".research_plugin":
        raise ValidationError("resource path may not point inside .research_plugin")
    return rel.as_posix()


def content_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

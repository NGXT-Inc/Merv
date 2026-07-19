"""Wire-level resource observation records shared by both planes."""

from __future__ import annotations

from typing import TypedDict


class ResourceObservation(TypedDict):
    """Repo-relative file facts submitted by the data plane."""

    path: str
    kind: str
    title: str
    created_by: str
    mtime_ns: int
    ctime_ns: int
    size_bytes: int
    content_sha256: str
    content_type: str

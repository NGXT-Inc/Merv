"""Provider port for heavy, content-addressed object storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypedDict


@dataclass(frozen=True)
class ObjectStat:
    sha256: str
    namespace: str
    size_bytes: int
    content_type: str
    created_at: str


class UploadPart(TypedDict):
    part_number: int
    url: str


class _UploadIdentity(TypedDict):
    upload_id: str


class UploadTarget(_UploadIdentity, total=False):
    """Adapter-minted target for a single or multipart heavy-object upload."""

    url: str
    parts: list[UploadPart]
    part_size: int
    size_bytes: int
    content_type: str
    checksum_sha256: str
    expires_in: int


class DownloadTarget(TypedDict):
    """Adapter-minted target for downloading a heavy object."""

    url: str


class ObjectStore(Protocol):
    """Heavy object storage: producers move bytes; control mints URLs and verifies."""

    def presign_upload(
        self,
        *,
        namespace: str,
        sha256: str,
        size_bytes: int,
        content_type: str = "application/octet-stream",
        expires_in: int,
    ) -> UploadTarget:
        ...

    def complete_upload(
        self, *, upload_id: str, parts: list[dict[str, Any]] | None = None
    ) -> ObjectStat:
        ...

    def presign_download(
        self, *, namespace: str, sha256: str, expires_in: int
    ) -> DownloadTarget:
        ...

    def stat(self, *, namespace: str, sha256: str) -> ObjectStat | None: ...

    def delete(self, *, namespace: str, sha256: str) -> bool: ...


__all__ = ["DownloadTarget", "ObjectStat", "ObjectStore", "UploadPart", "UploadTarget"]

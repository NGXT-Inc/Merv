"""Ports and value objects for submitted, content-addressed evidence bytes.

Business components need only :class:`EvidenceBlobStore`.  Expiry cleanup and
off-process transfer are separate capabilities so callers do not accidentally
depend on adapter-management operations.  ``BlobStore`` preserves the
historical all-capabilities protocol for compatibility and provider factories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, TypedDict

from ..utils import ValidationError


_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class BlobStat:
    """Metadata for one namespace-scoped submitted blob."""

    sha256: str
    namespace: str
    size_bytes: int
    content_type: str
    created_at: str
    expires_at: str | None


class BlobDownloadTarget(TypedDict):
    """Adapter-minted target for downloading submitted bytes."""

    url: str


class BlobUploadTarget(TypedDict):
    """Adapter-minted, single-use target for uploading submitted bytes."""

    upload_id: str
    url: str
    max_size_bytes: int
    expires_at: str | None


class EvidenceBlobStore(Protocol):
    """Small byte-storage port used by Artifacts and Feed."""

    def put(
        self,
        *,
        namespace: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        expires_at: str | None = None,
    ) -> str:
        """Store bytes and return their sha256 key."""
        ...

    def get(self, *, namespace: str, sha256: str) -> bytes:
        """Return submitted bytes, raising ``NotFoundError`` when absent."""
        ...

    def stat(self, *, namespace: str, sha256: str) -> BlobStat | None: ...


class ExpiringBlobStore(Protocol):
    """Cleanup-only capability for removing submitted bytes past their TTL."""

    def sweep_expired(self, *, now: str | None = None) -> int: ...


class BlobTransferStore(Protocol):
    """Optional adapter capability for off-process transfer and deletion."""

    def presign_get(
        self, *, namespace: str, sha256: str
    ) -> BlobDownloadTarget: ...

    def delete(self, *, namespace: str, sha256: str) -> bool: ...

    def presign_put(
        self,
        *,
        namespace: str,
        max_size_bytes: int,
        expires_at: str | None = None,
        content_type: str = "application/octet-stream",
    ) -> BlobUploadTarget: ...

    def finalize_put(self, *, upload_id: str) -> BlobStat: ...


class BlobStore(
    EvidenceBlobStore, ExpiringBlobStore, BlobTransferStore, Protocol
):
    """Compatibility composite implemented by existing local and S3 stores."""


def validate_blob_keys(*, namespace: str, sha256: str | None = None) -> None:
    """Validate a submitted-byte namespace and optional content key."""

    if not namespace or not _NAMESPACE_RE.match(namespace):
        raise ValidationError(f"invalid blob namespace: {namespace!r}")
    if sha256 is not None and not _SHA256_RE.match(sha256):
        raise ValidationError(f"invalid blob key (expected sha256 hex): {sha256!r}")


__all__ = [
    "BlobDownloadTarget",
    "BlobStat",
    "BlobStore",
    "BlobTransferStore",
    "BlobUploadTarget",
    "EvidenceBlobStore",
    "ExpiringBlobStore",
    "validate_blob_keys",
]

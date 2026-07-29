"""Ports and value objects for submitted, content-addressed evidence bytes.

Business components need only :class:`EvidenceBlobStore`; expiry cleanup is a
separate capability so content owners do not depend on adapter maintenance.
"""

from __future__ import annotations

import re
from typing import Protocol

from ..utils import ValidationError


_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceBlobStore(Protocol):
    """The complete byte-storage surface used by Artifacts and Feed."""

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

class ExpiringBlobStore(Protocol):
    """Cleanup-only capability for removing submitted bytes past their TTL."""

    def sweep_expired(self, *, now: str | None = None) -> int: ...


class BlobStore(EvidenceBlobStore, ExpiringBlobStore, Protocol):
    """Composition-time submitted-byte provider."""


def validate_blob_keys(*, namespace: str, sha256: str | None = None) -> None:
    """Validate a submitted-byte namespace and optional content key."""

    if not namespace or not _NAMESPACE_RE.match(namespace):
        raise ValidationError(f"invalid blob namespace: {namespace!r}")
    if sha256 is not None and not _SHA256_RE.match(sha256):
        raise ValidationError(f"invalid blob key (expected sha256 hex): {sha256!r}")


__all__ = [
    "BlobStore",
    "EvidenceBlobStore",
    "ExpiringBlobStore",
    "validate_blob_keys",
]

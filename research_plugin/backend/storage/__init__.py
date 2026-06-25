"""Heavy-file object store providers."""

from __future__ import annotations

from .local_object_store import LocalObjectStore
from .s3_object_store import S3CompatibleObjectStore

__all__ = ["LocalObjectStore", "S3CompatibleObjectStore"]

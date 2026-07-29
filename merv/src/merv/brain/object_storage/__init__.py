# If you update this file, you must consult object_storage.md to see whether object_storage.md needs to be updated. object_storage.md must not exceed 100 lines.
"""Object Storage lifecycle root."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .storage import ObjectStorage


def __getattr__(name: str) -> Any:
    """Preserve the released export without eagerly importing its adapter."""
    if name == "S3CompatibleObjectStore":
        module = import_module(f"{__name__}.s3_object_store")
        return module.S3CompatibleObjectStore
    raise AttributeError(name)


__all__ = ["ObjectStorage", "S3CompatibleObjectStore"]

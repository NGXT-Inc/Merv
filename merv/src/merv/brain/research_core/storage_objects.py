"""Research-owned query seam for heavy objects produced by an experiment."""

from __future__ import annotations

from typing import Any, Protocol

from ..kernel.state.store import Connection


class StorageObjectsReader(Protocol):
    """Read experiment storage rows inside the caller's current transaction."""

    def __call__(
        self, *, conn: Connection, project_id: str, experiment_id: str
    ) -> list[dict[str, Any]]: ...


__all__ = ["StorageObjectsReader"]

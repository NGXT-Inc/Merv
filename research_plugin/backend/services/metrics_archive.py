"""Service-side port for sandbox metrics archives."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class MetricsArchive(Protocol):
    """Durable per-experiment metrics snapshot cache used by sandbox services."""

    def path_for(self, experiment_id: str) -> Path: ...

    def persist(self, *, experiment_id: str, snapshot: dict[str, Any]) -> Path: ...

    def load(self, *, experiment_id: str) -> dict[str, Any] | None: ...

"""Ports for sandbox lifecycle collaborators."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class ProvisionReaper(Protocol):
    def reap_stale_provisions(
        self, *, now: datetime, deadline_seconds: float
    ) -> int:
        ...

    def cleanup_orphan(
        self, *, experiment_id: str, row: dict[str, Any] | None
    ) -> None:
        ...

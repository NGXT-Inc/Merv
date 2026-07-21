"""Explicit application query for the durable project event timeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class EventTimelineSource(Protocol):
    def project_event_signal(self, *, project_id: str) -> str: ...
    def recent_events(self, *, project_id: str, limit: int) -> dict[str, Any]: ...
    def events_since(self, *, project_id: str, after_id: int) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class EventTimelineQuery:
    """Read events without exposing the state store to delivery code."""

    source: EventTimelineSource

    def signal(self, *, project_id: str) -> str:
        return self.source.project_event_signal(project_id=project_id)

    def recent(self, *, project_id: str, limit: int) -> dict[str, Any]:
        return self.source.recent_events(project_id=project_id, limit=limit)

    def since(self, *, project_id: str, after_id: int) -> dict[str, Any]:
        return self.source.events_since(project_id=project_id, after_id=after_id)

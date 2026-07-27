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
        # Read past hidden legacy rows so callers still receive the requested
        # number of current product events where possible.
        result = self.source.recent_events(project_id=project_id, limit=500)
        events = _visible_events(result.get("events") or [])[:limit]
        return {**result, "events": events}

    def since(self, *, project_id: str, after_id: int) -> dict[str, Any]:
        result = self.source.events_since(project_id=project_id, after_id=after_id)
        return {**result, "events": _visible_events(result.get("events") or [])}


def _visible_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hide dormant integration events and fields without deleting history."""

    visible = []
    for event in events:
        if "mlflow" in str(event.get("type") or "").lower():
            continue
        visible.append(_strip_legacy_fields(event))
    return visible


def _strip_legacy_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_legacy_fields(item)
            for key, item in value.items()
            if "mlflow" not in str(key).lower()
        }
    if isinstance(value, list):
        return [_strip_legacy_fields(item) for item in value]
    return value

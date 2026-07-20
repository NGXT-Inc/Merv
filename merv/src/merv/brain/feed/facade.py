"""Stable Feed entrypoint for cross-component workflows."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .feed import FeedService


@runtime_checkable
class Feed(Protocol):
    def transition_advisory(
        self, *, project_id: str, experiment_id: str, event: str
    ) -> str | None: ...


class FeedFacade:
    """Narrow adapter over the already-composed feed service."""

    __slots__ = ("_feed",)

    def __init__(self, feed: FeedService) -> None:
        self._feed = feed

    def transition_advisory(
        self, *, project_id: str, experiment_id: str, event: str
    ) -> str | None:
        return self._feed.feed_note_for(
            project_id=project_id, entity_id=experiment_id, event=event
        )


__all__ = ["Feed", "FeedFacade"]

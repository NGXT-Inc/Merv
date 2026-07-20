"""Small synchronous sequencer for reactions to one committed event."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from ..kernel.events import StoredEvent


StateT = TypeVar("StateT")


@dataclass(frozen=True, slots=True)
class EventContext(Generic[StateT]):
    event: StoredEvent
    state: StateT


@dataclass(frozen=True, slots=True)
class EventReaction(Generic[StateT]):
    state: StateT
    value: object | None = None


@dataclass(frozen=True, slots=True)
class DispatchResult(Generic[StateT]):
    state: StateT
    outcomes: Mapping[str, object]


EventHandler = Callable[[EventContext[Any]], EventReaction[Any]]


class EventDispatcher:
    """Explicit registration and ordered dispatch; no persistence or scanning."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], list[tuple[str, EventHandler]]] = {}

    def register(
        self,
        *,
        event_type: str,
        phase: str,
        name: str,
        handler: EventHandler,
    ) -> None:
        key = (event_type.strip(), phase.strip())
        stable_name = name.strip()
        if not key[0] or not key[1] or not stable_name:
            raise ValueError("event_type, phase, and handler name are required")
        handlers = self._handlers.setdefault(key, [])
        if any(registered == stable_name for registered, _handler in handlers):
            raise ValueError(
                f"duplicate event handler {stable_name!r} for {key[0]!r}/{key[1]!r}"
            )
        handlers.append((stable_name, handler))

    def dispatch(
        self, *, event: StoredEvent, phase: str, state: StateT
    ) -> DispatchResult[StateT]:
        current: Any = state
        outcomes: dict[str, object] = {}
        for name, handler in self._handlers.get((event.type, phase), ()):
            reaction = handler(EventContext(event=event, state=current))
            current = reaction.state
            if reaction.value is not None:
                outcomes[name] = reaction.value
        return DispatchResult(
            state=current,
            outcomes=MappingProxyType(outcomes),
        )


__all__ = [
    "DispatchResult",
    "EventContext",
    "EventDispatcher",
    "EventReaction",
]

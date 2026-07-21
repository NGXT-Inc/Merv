"""Synchronous committed-event reactions: fatal errors propagate, advisory
errors drop, and any future replay must checkpoint ``(event.id, phase, handler)``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Generic, Literal, TypeVar

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
FailureMode = Literal["fatal", "advisory"]
IdempotencyMode = Literal["repeat_safe", "requires_adapter_key_for_redelivery"]
_DELIVERY_POLICIES = {
    ("fatal", "repeat_safe"),
    ("fatal", "requires_adapter_key_for_redelivery"),
    ("advisory", "repeat_safe"),
}


@dataclass(frozen=True, slots=True)
class EventCatalogEntry:
    """One reaction; producer names the method committing state and event."""

    producer: str
    event_type: str
    payload_version: int
    transaction_boundary: str
    reaction_phase: str
    handler_identity: str
    failure: FailureMode
    idempotency: IdempotencyMode

    def __post_init__(self) -> None:
        if (
            not all(value.strip() for value in (
                self.producer, self.event_type, self.transaction_boundary,
                self.reaction_phase, self.handler_identity,
            ))
            or self.payload_version < 1
            or (self.failure, self.idempotency) not in _DELIVERY_POLICIES
        ):
            raise ValueError("invalid event catalog entry")


class EventDispatcher:
    """Explicit ordered registry; no persistence, scanning, replay, or worker."""

    def __init__(self) -> None:
        self._catalog: tuple[EventCatalogEntry, ...] = ()
        self._handlers: tuple[tuple[EventCatalogEntry, EventHandler], ...] = ()

    @property
    def catalog(self) -> tuple[EventCatalogEntry, ...]:
        return self._catalog

    def bind_catalog(
        self,
        catalog: tuple[EventCatalogEntry, ...],
        *,
        handlers: Mapping[str, EventHandler],
    ) -> None:
        """Bind a complete catalog or fail before registering any handler."""

        expected = {entry.handler_identity for entry in catalog}
        if set(handlers) != expected:
            raise ValueError("event catalog handler mismatch")
        if self._handlers:
            raise ValueError("event catalog is already bound")
        keys = [
            (entry.event_type, entry.reaction_phase, entry.handler_identity)
            for entry in catalog
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate event catalog registration")
        if any(not callable(handler) for handler in handlers.values()):
            raise TypeError("event catalog handlers must be callable")
        self._catalog = catalog
        self._handlers = tuple(
            (entry, handlers[entry.handler_identity]) for entry in catalog
        )

    def dispatch(
        self, *, event: StoredEvent, phase: str, state: StateT
    ) -> DispatchResult[StateT]:
        current: Any = state
        outcomes: dict[str, object] = {}
        for entry, handler in self._handlers:
            if (entry.event_type, entry.reaction_phase) != (event.type, phase):
                continue
            try:
                reaction = handler(EventContext(event=event, state=current))
            except Exception:
                if entry.failure == "advisory":
                    continue
                raise
            current = reaction.state
            if reaction.value is not None:
                outcomes[entry.handler_identity] = reaction.value
        return DispatchResult(state=current, outcomes=MappingProxyType(outcomes))

"""Research-owned values exposed when a workflow transition commits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..kernel.events import StoredEvent


@dataclass(frozen=True, slots=True)
class CommittedExperimentTransition:
    state: dict[str, Any]
    event: StoredEvent


__all__ = ["CommittedExperimentTransition"]

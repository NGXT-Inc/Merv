"""Ports used by resource record services."""

from __future__ import annotations

from typing import Protocol

from merv.shared.resource_records import ResourceObservation


class ResourceObserver(Protocol):
    """Local data-plane observation required before resource recording."""

    def observe_file(
        self,
        *,
        path: str,
        kind: str = "other",
        title: str = "",
        created_by: str = "codex",
    ) -> ResourceObservation: ...


class ResourceAssociationPolicy(Protocol):
    """Resource association validation required by resource recording."""

    def validate_resource_association(self, *, target_type: str, role: str) -> None: ...

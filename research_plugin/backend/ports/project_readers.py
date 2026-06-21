"""Ports used by project overview projections."""

from __future__ import annotations

from typing import Any, Protocol


class ProjectCurrentReader(Protocol):
    """Provides the current project identity."""

    def current(self, *, tenant_id: str | None = None) -> dict[str, Any]:
        ...


class SynthesisOverviewReader(Protocol):
    """Provides reflection-wave summaries for project overview."""

    def latest_published(self, *, conn: Any, project_id: str) -> dict[str, Any] | None:
        ...

    def open_synthesis(self, *, conn: Any, project_id: str) -> dict[str, Any] | None:
        ...

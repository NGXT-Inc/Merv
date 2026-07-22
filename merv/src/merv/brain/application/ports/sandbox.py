from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SandboxReads(Protocol):
    def for_experiment(
        self, *, project_id: str, experiment_id: str
    ) -> list[dict[str, Any]]: ...

    def for_project(self, *, project_id: str) -> list[dict[str, Any]]: ...


__all__ = ["SandboxReads"]

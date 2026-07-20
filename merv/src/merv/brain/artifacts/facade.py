"""Stable Artifacts entrypoint for cross-component workflows."""

from __future__ import annotations

from typing import Protocol, TypedDict, cast, runtime_checkable

from .resources import ResourceService


class MetricFileSource(TypedDict):
    path: str
    version_id: str
    sha256: str
    observed_at: str
    data: object


@runtime_checkable
class Artifacts(Protocol):
    def metric_file_sources(
        self, *, experiment_id: str, attempt_index: int
    ) -> list[MetricFileSource]: ...

    def pin_system_artifact(
        self,
        *,
        path: str,
        experiment_id: str,
        role: str,
        content_bytes: bytes,
        content_type: str,
        title: str,
        kind: str,
        project_id: str,
    ) -> None: ...


class ArtifactsFacade:
    """Narrow adapter over the already-composed resource service."""

    __slots__ = ("_resources",)

    def __init__(self, resources: ResourceService) -> None:
        self._resources = resources

    def metric_file_sources(
        self, *, experiment_id: str, attempt_index: int
    ) -> list[MetricFileSource]:
        return cast(
            list[MetricFileSource],
            self._resources.metric_file_sources(
                target_id=experiment_id, attempt_index=attempt_index
            ),
        )

    def pin_system_artifact(
        self,
        *,
        path: str,
        experiment_id: str,
        role: str,
        content_bytes: bytes,
        content_type: str,
        title: str,
        kind: str,
        project_id: str,
    ) -> None:
        self._resources.pin_system_artifact(
            path=path,
            target_type="experiment",
            target_id=experiment_id,
            role=role,
            content_bytes=content_bytes,
            content_type=content_type,
            title=title,
            kind=kind,
            project_id=project_id,
        )


__all__ = ["Artifacts", "ArtifactsFacade", "MetricFileSource"]

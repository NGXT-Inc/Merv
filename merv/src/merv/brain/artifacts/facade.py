"""Stable Artifacts entrypoint for cross-component workflows."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict, cast, runtime_checkable

from ..kernel.utils import NotFoundError
from .submissions import ArtifactSubmissionService, upload_command


@runtime_checkable
class ArtifactSubmissions(Protocol):
    """Public artifact submit/upload/read contract for delivery adapters."""

    def submit(self, **kwargs: Any) -> dict[str, Any]: ...
    def complete_upload(self, *, token: str, data: bytes) -> dict[str, Any]: ...
    def complete_figure_upload(self, *, token: str, data: bytes) -> dict[str, Any]: ...
    def find(self, **kwargs: Any) -> dict[str, Any]: ...
    def artifact_content(self, **kwargs: Any) -> dict[str, Any]: ...
    def artifact_file(self, **kwargs: Any) -> tuple[bytes, str, str]: ...
    def figure_bytes(self, **kwargs: Any) -> bytes | None: ...


class MetricFileSource(TypedDict):
    path: str
    artifact_id: str
    sha256: str
    submitted_at: str
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
        project_id: str,
    ) -> None: ...

    def submitted_text_for_artifact(self, *, artifact_id: str | None) -> str | None: ...

    def submitted_artifact_figure(
        self, *, project_id: str, artifact_id: str, link_path: str
    ) -> tuple[bytes, str] | None: ...

    def resolve_artifact_reference(
        self, *, project_id: str, artifact_id: str
    ) -> dict[str, Any] | None: ...


class ArtifactsFacade:
    """Narrow adapter over the artifact submission service."""

    __slots__ = ("_submissions",)

    def __init__(self, *, submissions: ArtifactSubmissionService) -> None:
        self._submissions = submissions

    def metric_file_sources(
        self, *, experiment_id: str, attempt_index: int
    ) -> list[MetricFileSource]:
        return cast(
            list[MetricFileSource],
            self._submissions.metric_sources(
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
        project_id: str,
    ) -> None:
        self._submissions.pin_system_artifact(
            path=path,
            target_type="experiment",
            target_id=experiment_id,
            role=role,
            content_bytes=content_bytes,
            content_type=content_type,
            title=title,
            project_id=project_id,
        )

    def submitted_text_for_artifact(self, *, artifact_id: str | None) -> str | None:
        return self._submissions.submitted_text_for_artifact(artifact_id=artifact_id)

    def submitted_artifact_figure(
        self, *, project_id: str, artifact_id: str, link_path: str
    ) -> tuple[bytes, str] | None:
        data = self._submissions.figure_bytes(
            project_id=project_id, artifact_id=artifact_id, link_path=link_path
        )
        return (data, link_path.rsplit("/", 1)[-1]) if data is not None else None

    def resolve_artifact_reference(
        self, *, project_id: str, artifact_id: str
    ) -> dict[str, Any] | None:
        """Resolve one artifact id for graph-node refs; None when unknown."""
        try:
            artifact = self._submissions.resolve(
                artifact_id=artifact_id, project_id=project_id
            )
        except NotFoundError:
            return None
        return {
            "type": "artifact",
            "resolved": True,
            "artifact_id": artifact["id"],
            "path": artifact.get("path"),
            "role": artifact.get("role"),
            "title": artifact.get("title"),
        }


__all__ = [
    "ArtifactSubmissions",
    "Artifacts",
    "ArtifactsFacade",
    "MetricFileSource",
    "upload_command",
]

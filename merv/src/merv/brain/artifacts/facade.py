"""Stable Artifacts entrypoint for cross-component workflows."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict, cast, runtime_checkable

from merv.shared.artifact_roles import GATED_ROLES

from ..kernel.utils import NotFoundError
from .resources import ResourceService
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

    def resolve_resource(
        self, *, project_id: str, resource_id: str
    ) -> dict[str, Any]: ...

    def select_resource_text(
        self, *, resource: dict[str, Any], version_id: str | None = None
    ) -> tuple[str, str] | None: ...

    def submitted_figure(
        self, *, resource: dict[str, Any], link_path: str
    ) -> tuple[bytes, str] | None: ...

    def submitted_text_for_version(self, *, version_id: str | None) -> str | None: ...

    def resolve_resource_reference(
        self, *, project_id: str, ref: str
    ) -> dict[str, Any] | None: ...


class ArtifactsFacade:
    """Narrow adapter over the composed artifact services.

    Submitted-artifact reads and system pinning run on the new submission
    service; the resource-shaped methods below delegate to the legacy resource
    service only for the pre-cut HTTP routes and die with it in the deletion
    phase."""

    __slots__ = ("_resources", "_submissions")

    def __init__(
        self, resources: ResourceService, *, submissions: ArtifactSubmissionService
    ) -> None:
        self._resources = resources
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

    def resolve_resource(
        self, *, project_id: str, resource_id: str
    ) -> dict[str, Any]:
        return self._resources.resolve(
            project_id=project_id, resource_id=resource_id
        )

    def select_resource_text(
        self, *, resource: dict[str, Any], version_id: str | None = None
    ) -> tuple[str, str] | None:
        """Authorize and select submitted text without shaping a response."""
        if version_id:
            valid = {
                str(association.get("version_id"))
                for association in resource.get("associations", [])
                if association.get("version_id")
            }
            current_version = resource.get("current_version_id")
            if current_version:
                valid.add(str(current_version))
            if version_id not in valid:
                raise NotFoundError(
                    f"version {version_id} is not associated with resource "
                    f"{resource.get('id')}"
                )
            return (
                self._resources.pinned_text_for_version(
                    version_id=version_id, what="resource content", role=""
                ),
                version_id,
            )
        return self._pinned_gated_text(resource=resource)

    def submitted_figure(
        self, *, resource: dict[str, Any], link_path: str
    ) -> tuple[bytes, str] | None:
        version_id = self._latest_gated_version_id(resource=resource)
        if version_id is None:
            return None
        data = self._resources.submitted_figure(
            version_id=version_id, link_path=link_path
        )
        return (data, link_path.rsplit("/", 1)[-1]) if data is not None else None

    def submitted_text_for_version(self, *, version_id: str | None) -> str | None:
        return self._resources.submitted_text_for_version(version_id=version_id)

    def resolve_resource_reference(
        self, *, project_id: str, ref: str
    ) -> dict[str, Any] | None:
        return self._resources.resolve_resource_reference(
            project_id=project_id, ref=ref
        )

    @staticmethod
    def _latest_gated_version_id(*, resource: dict[str, Any]) -> str | None:
        best: tuple[int, str] | None = None
        for association in resource.get("associations", []):
            role = str(association.get("role") or "")
            version_id = association.get("version_id")
            if role not in GATED_ROLES or not version_id:
                continue
            candidate = (int(association.get("attempt_index") or 0), str(version_id))
            if best is None or candidate[0] >= best[0]:
                best = candidate
        return best[1] if best else None

    def _pinned_gated_text(
        self, *, resource: dict[str, Any]
    ) -> tuple[str, str] | None:
        latest_association = self._latest_gated_version_id(resource=resource)
        if latest_association is None:
            return None
        candidates = [str(resource["current_version_id"])] if resource.get(
            "current_version_id"
        ) else []
        if latest_association not in candidates:
            candidates.append(latest_association)
        for version_id in candidates:
            text = self._resources.submitted_text_for_version(version_id=version_id)
            if text is not None:
                return text, version_id
        return None


class ArtifactRecords(Protocol):
    """Public record API for single-component delivery operations."""

    def list_resources(self, **kwargs: Any) -> dict[str, Any]: ...
    def resolve(self, **kwargs: Any) -> dict[str, Any]: ...
    def validate_association_intent(self, **kwargs: Any) -> dict[str, Any]: ...
    def record_observation(self, **kwargs: Any) -> dict[str, Any]: ...
    def associate_observed(self, **kwargs: Any) -> dict[str, Any]: ...
    def delete(self, **kwargs: Any) -> dict[str, Any]: ...


__all__ = [
    "ArtifactRecords",
    "ArtifactSubmissions",
    "Artifacts",
    "ArtifactsFacade",
    "MetricFileSource",
    "upload_command",
]

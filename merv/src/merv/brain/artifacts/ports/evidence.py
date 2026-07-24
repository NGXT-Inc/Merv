"""Public evidence facts shared with Research.

The contract deliberately exposes immutable submitted evidence, not database
connections, blob locators, or Artifact persistence tables.  Its concrete
implementation remains in Artifacts; Research only decides when the evidence
is required and what workflow policy applies to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


# Matches every gated artifact upload/content endpoint ceiling. Kept explicit
# so this cross-component port remains a dependency-free value contract.
MAX_SUBMITTED_TEXT_BYTES = 16_000


@dataclass(frozen=True, slots=True)
class AssociatedEvidence:
    """One complete submitted artifact expressed without persistence names."""

    artifact_id: str
    project_id: str
    role: str
    attempt_index: int
    lens_id: str
    path: str
    title: str
    content_sha256: str
    size_bytes: int
    content_type: str
    created_by: str
    created_at: str
    updated_at: str
    order: int


@dataclass(frozen=True, slots=True)
class SubmittedDocument:
    text: str
    artifact_id: str
    path: str
    role: str
    figure_links: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SubmittedEvidence:
    """Best-effort submitted text for one immutable artifact."""

    role: str
    path: str
    artifact_id: str
    order: int
    content: str | None


@dataclass(frozen=True, slots=True)
class SubmittedContent:
    """Bounded best-effort text for one immutable submitted artifact."""

    artifact_id: str
    content: str | None
    size_bytes: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class AssociationTarget:
    project_id: str | None
    attempt_index: int


@runtime_checkable
class EvidenceReader(Protocol):
    """Immutable Artifact evidence consumed by Research policy."""

    def artifacts_for_target(
        self, *, target_type: str, target_id: str
    ) -> tuple[AssociatedEvidence, ...]: ...

    def artifacts_for_targets(
        self, *, target_type: str, target_ids: tuple[str, ...]
    ) -> dict[str, tuple[AssociatedEvidence, ...]]: ...

    def submitted_document(
        self, *, artifact_id: str | None, what: str
    ) -> SubmittedDocument: ...

    def bounded_text_for_artifact(self, *, artifact_id: str) -> SubmittedContent: ...

    def submitted_evidence(
        self,
        *,
        target_type: str,
        target_id: str,
        attempt_index: int,
        roles: tuple[str, ...],
    ) -> tuple[SubmittedEvidence, ...]: ...


@runtime_checkable
class AssociationTargetResolver(Protocol):
    """Research-owned target facts needed while submitting an artifact."""

    def resolve(self, *, target_type: str, target_id: str) -> AssociationTarget: ...

    def publish_pinned_artifact_ids(self, *, conn: object) -> frozenset[str]: ...


__all__ = [
    "AssociatedEvidence",
    "AssociationTarget",
    "AssociationTargetResolver",
    "EvidenceReader",
    "MAX_SUBMITTED_TEXT_BYTES",
    "SubmittedContent",
    "SubmittedDocument",
    "SubmittedEvidence",
]

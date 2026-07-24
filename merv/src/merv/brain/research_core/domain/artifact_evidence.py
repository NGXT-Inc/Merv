"""Research policy and response shaping for Artifact evidence facts."""

from __future__ import annotations

from typing import Any

from ...artifacts.ports import AssociatedEvidence


def artifact_submission_recency_key(
    artifact: dict[str, Any],
) -> tuple[int, str, str, str]:
    """Stable newest-submission ordering for immutable artifact evidence."""
    return (
        int(artifact.get("submitted_order") or 0),
        str(artifact.get("updated_at") or artifact.get("created_at") or ""),
        str(artifact.get("id") or artifact.get("artifact_id") or ""),
        str(artifact.get("path") or ""),
    )


def artifact_state_record(evidence: AssociatedEvidence) -> dict[str, Any]:
    """Project one submitted artifact into the public Research record shape.

    `id` is the artifact id; these key names are the stable shape state
    consumers (gates, guidance, UI projections) read."""
    return {
        "id": evidence.artifact_id,
        "project_id": evidence.project_id,
        "path": evidence.path,
        "title": evidence.title,
        "lens_id": evidence.lens_id,
        "size_bytes": evidence.size_bytes,
        "content_type": evidence.content_type,
        "created_by": evidence.created_by,
        "created_at": evidence.created_at,
        "updated_at": evidence.updated_at,
        "role": evidence.role,
        "attempt_index": evidence.attempt_index,
        "submitted_order": evidence.order,
    }


def preferred_associated_artifact(
    *,
    artifacts: list[dict[str, Any]],
    attempt: Any,
    roles: tuple[str, ...],
) -> dict[str, Any] | None:
    """Select current-attempt evidence by role precedence and submission age."""
    candidates = [
        artifact
        for artifact in artifacts
        if artifact.get("role") in roles
    ]
    if not candidates:
        return None
    current = [
        artifact
        for artifact in candidates
        if artifact.get("attempt_index") == attempt
    ]
    eligible = current or candidates
    role_rank = {role: index for index, role in enumerate(roles)}
    preferred_rank = min(
        role_rank.get(str(artifact.get("role")), len(roles))
        for artifact in eligible
    )
    return max(
        (
            artifact
            for artifact in eligible
            if role_rank.get(str(artifact.get("role")), len(roles))
            == preferred_rank
        ),
        key=artifact_submission_recency_key,
    )


__all__ = [
    "artifact_state_record",
    "artifact_submission_recency_key",
    "preferred_associated_artifact",
]

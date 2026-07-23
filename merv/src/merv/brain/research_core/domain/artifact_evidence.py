"""Research policy and response shaping for Artifact evidence facts."""

from __future__ import annotations

from typing import Any

from ...artifacts.ports import AssociatedEvidence


def artifact_state_record(evidence: AssociatedEvidence) -> dict[str, Any]:
    """Project one submitted artifact into the public Research record shape.

    `id` is the artifact id; the association_* key names are the stable shape
    state consumers (gates, guidance, UI projections) read."""
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
        "association_role": evidence.role,
        "association_attempt_index": evidence.attempt_index,
        "association_rowid": evidence.order,
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
        if artifact.get("association_role") in roles
    ]
    if not candidates:
        return None
    current = [
        artifact
        for artifact in candidates
        if artifact.get("association_attempt_index") == attempt
    ]
    role_rank = {role: index for index, role in enumerate(roles)}
    return min(
        current or candidates,
        key=lambda artifact: (
            role_rank.get(str(artifact.get("association_role")), len(roles)),
            -(artifact.get("association_rowid") or 0),
        ),
    )


__all__ = ["artifact_state_record", "preferred_associated_artifact"]

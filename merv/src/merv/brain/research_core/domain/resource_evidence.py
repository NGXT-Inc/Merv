"""Research policy and response shaping for Artifact evidence facts."""

from __future__ import annotations

from typing import Any

from ...artifacts.ports import AssociatedEvidence


def resource_state_record(evidence: AssociatedEvidence) -> dict[str, Any]:
    """Preserve the public Research resource shape at the component edge."""
    return {
        "id": evidence.resource_id,
        "project_id": evidence.project_id,
        "path": evidence.path,
        "kind": evidence.kind,
        "title": evidence.title,
        "current_version_id": evidence.current_version_id,
        "version_token": evidence.version_token,
        "mtime_ns": evidence.modified_time_ns,
        "size_bytes": evidence.size_bytes,
        "observed_at": evidence.observed_at,
        "git_commit": evidence.git_commit,
        "missing": int(evidence.is_missing),
        "deleted": int(evidence.is_deleted),
        "created_by": evidence.created_by,
        "created_at": evidence.created_at,
        "updated_at": evidence.updated_at,
        "association_role": evidence.role,
        "association_attempt_index": evidence.attempt_index,
        "association_version_id": evidence.submitted_version_id,
        "association_rowid": evidence.association_order,
    }


def preferred_associated_resource(
    *,
    resources: list[dict[str, Any]],
    attempt: Any,
    roles: tuple[str, ...],
) -> dict[str, Any] | None:
    """Select current-attempt evidence by role precedence and association age."""
    candidates = [
        resource
        for resource in resources
        if resource.get("association_role") in roles
    ]
    if not candidates:
        return None
    current = [
        resource
        for resource in candidates
        if resource.get("association_attempt_index") == attempt
    ]
    role_rank = {role: index for index, role in enumerate(roles)}
    return min(
        current or candidates,
        key=lambda resource: (
            role_rank.get(str(resource.get("association_role")), len(roles)),
            -(resource.get("association_rowid") or 0),
        ),
    )


__all__ = ["preferred_associated_resource", "resource_state_record"]

"""Artifact-association vocabulary validation owned by Artifacts."""

from __future__ import annotations

from merv.shared.artifact_roles import (
    ARTIFACT_TARGET_TYPES,
    LEGACY_PROJECT_GRAPH_ROLE,
    LEGACY_ROLE_REPLACEMENTS,
    PROJECT_GRAPH_ROLE,
    SUBMITTABLE_ROLES,
)

from ..kernel.utils import ValidationError


def validate_artifact_association(*, target_type: str, role: str) -> None:
    if target_type not in ARTIFACT_TARGET_TYPES:
        allowed = sorted(ARTIFACT_TARGET_TYPES)
        raise ValidationError(
            f"unknown artifact target type: {target_type}. "
            f"Allowed target types: {', '.join(allowed)}",
            details={"allowed_target_types": allowed},
        )
    if role in LEGACY_ROLE_REPLACEMENTS:
        replacement = LEGACY_ROLE_REPLACEMENTS[role]
        raise ValidationError(
            f"legacy artifact role {role!r} is read-only for old records; "
            f"use {replacement!r}",
            details={"legacy_role": role, "replacement_role": replacement},
        )
    if target_type == "reflection" and role == LEGACY_PROJECT_GRAPH_ROLE:
        raise ValidationError(
            "use role 'project_graph' for reflection-wave project graphs; "
            "role 'graph' is only for experiment logic graphs",
            details={
                "legacy_role": LEGACY_PROJECT_GRAPH_ROLE,
                "replacement_role": PROJECT_GRAPH_ROLE,
            },
        )
    if role not in SUBMITTABLE_ROLES:
        allowed = sorted(SUBMITTABLE_ROLES)
        raise ValidationError(
            f"unknown artifact role: {role}. Allowed roles: {', '.join(allowed)}",
            details={
                "allowed_roles": allowed,
                "recommended_result_role": "result",
            },
        )

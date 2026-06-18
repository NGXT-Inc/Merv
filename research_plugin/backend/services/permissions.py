"""Permission and policy checks for v0.0001."""

from __future__ import annotations

from ..utils import PermissionDeniedError, ValidationError


REVIEW_ROLES = {
    "design_reviewer",
    "experiment_reviewer",
    "reflection_reviewer",
    "human",
    "automated_check",
}
REVIEW_VERDICTS = {"pass", "needs_changes", "fail"}
RESOURCE_TARGET_TYPES = {"experiment", "reflection", "claim", "review", "attempt"}
PROJECT_GRAPH_ROLE = "project_graph"
LEGACY_PROJECT_GRAPH_ROLE = "graph"
PROJECT_GRAPH_ROLES = (PROJECT_GRAPH_ROLE, LEGACY_PROJECT_GRAPH_ROLE)
REFLECTION_LENS_DOC_ROLE = "reflection_lens_doc"
LEGACY_REFLECTION_LENS_DOC_ROLE = "reflection"
REFLECTION_LENS_DOC_ROLES = (
    REFLECTION_LENS_DOC_ROLE,
    LEGACY_REFLECTION_LENS_DOC_ROLE,
)
LEGACY_REFLECTION_DOC_ROLE = "synthesis_doc"
LEGACY_PROPOSALS_ROLE = "proposals"
LEGACY_RESOURCE_ROLES = {
    LEGACY_REFLECTION_LENS_DOC_ROLE,
    LEGACY_REFLECTION_DOC_ROLE,
    LEGACY_PROPOSALS_ROLE,
}
RESOURCE_ROLES = {
    "plan", "input", "code", "config", "result", "report", "graph",
    PROJECT_GRAPH_ROLE,
    REFLECTION_LENS_DOC_ROLE, "reflection_doc",
    "change_spec",
    "note", "model", "other",
}

# Gated roles: the artifacts workflow gates lint. Associating one of these
# captures the file's bytes into the blob store (size-capped), pinning the
# association to immutable content (docs/CLOUD_BACKEND_MIGRATION_PLAN.md
# decision 6). The report/graph caps mirror artifacts.MAX_REPORT_BYTES and
# graph_lint.MAX_GRAPH_BYTES (alignment pinned by a structure test).
GATED_ROLE_BYTE_CAPS: dict[str, int] = {
    "plan": 16_000,
    "report": 16_000,
    "graph": 16_000,
    PROJECT_GRAPH_ROLE: 16_000,
    REFLECTION_LENS_DOC_ROLE: 16_000,
    "reflection_doc": 16_000,
    # Legacy alias accepted for waves created before the rename.
    "synthesis_doc": 16_000,
    "change_spec": 16_000,
    "proposals": 16_000,
    # Legacy alias accepted for per-lens docs created before the rename.
    "reflection": 16_000,
}
GATED_ROLES = frozenset(GATED_ROLE_BYTE_CAPS)


class PermissionService:
    """Small policy layer, intentionally separate from workflow and persistence."""

    def storage_resource_target_type(self, *, target_type: str) -> str:
        if target_type == "reflection":
            return "synthesis"
        return target_type

    def validate_review_role(self, *, role: str) -> None:
        if role not in REVIEW_ROLES:
            raise ValidationError(f"unknown review role: {role}")

    def validate_review_verdict(self, *, verdict: str) -> None:
        if verdict not in REVIEW_VERDICTS:
            raise ValidationError(f"unknown review verdict: {verdict}")

    def validate_resource_association(self, *, target_type: str, role: str) -> None:
        if target_type not in RESOURCE_TARGET_TYPES:
            allowed = sorted(RESOURCE_TARGET_TYPES)
            raise ValidationError(
                f"unknown resource target type: {target_type}. Allowed target types: {', '.join(allowed)}",
                details={"allowed_target_types": allowed},
            )
        if role in LEGACY_RESOURCE_ROLES:
            replacements = {
                LEGACY_REFLECTION_LENS_DOC_ROLE: REFLECTION_LENS_DOC_ROLE,
                LEGACY_REFLECTION_DOC_ROLE: "reflection_doc",
                LEGACY_PROPOSALS_ROLE: "change_spec",
            }
            replacement = replacements[role]
            raise ValidationError(
                f"legacy resource role {role!r} is read-only for old records; use {replacement!r}",
                details={
                    "legacy_role": role,
                    "replacement_role": replacement,
                },
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
        if role not in RESOURCE_ROLES:
            allowed = sorted(RESOURCE_ROLES)
            raise ValidationError(
                f"unknown resource role: {role}. Allowed roles: {', '.join(allowed)}",
                details={"allowed_resource_roles": allowed, "recommended_result_role": "result"},
            )

    def reject_reviewer_mutation(self, *, tool_name: str, review_session_id: str | None) -> None:
        if review_session_id and tool_name != "review.submit":
            raise PermissionDeniedError("review sessions are read-only except review.submit")

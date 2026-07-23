"""Artifact role and association-target vocabulary.

The artifacts module owns what an artifact may be called (roles), what it may
attach to (target types), and the per-role upload byte caps. This module is
deliberately dependency-free so research_core gates, surface adapters, and the
data plane can all share it without pulling services across feature boundaries.
"""

from __future__ import annotations

ARTIFACT_TARGET_TYPES = frozenset(
    {"experiment", "reflection", "claim", "review", "attempt"}
)

PROJECT_GRAPH_ROLE = "project_graph"
LEGACY_PROJECT_GRAPH_ROLE = "graph"
PROJECT_GRAPH_ROLES = (PROJECT_GRAPH_ROLE, LEGACY_PROJECT_GRAPH_ROLE)

REFLECTION_LENS_DOC_ROLE = "reflection_lens_doc"
LEGACY_REFLECTION_LENS_DOC_ROLE = "reflection"
REFLECTION_LENS_DOC_ROLES = (
    REFLECTION_LENS_DOC_ROLE,
    LEGACY_REFLECTION_LENS_DOC_ROLE,
)

# Pre-rename role spellings. Rejected at submit (with the replacement named)
# but still readable: artifact rows backfilled from pre-cut databases keep
# their original role strings.
LEGACY_ROLE_REPLACEMENTS = {
    LEGACY_REFLECTION_LENS_DOC_ROLE: REFLECTION_LENS_DOC_ROLE,
    "synthesis_doc": "reflection_doc",
    "proposals": "change_spec",
}

# System-authored role: the metrics exhibit is generated and pinned by the
# backend at submit_results. It is deliberately NOT submittable — agents
# cannot submit (and so cannot replace) it; only the system pin path may.
EXHIBIT_ROLE = "exhibit"
SYSTEM_CREATED_BY = "system"

# Role-'result' artifacts are small metrics JSON files the exhibit ingests.
METRIC_RESULT_MAX_BYTES = 16_000

# Roles an agent may submit via artifact.submit: the canonical gated docs plus
# the metrics-JSON 'result' role.
SUBMITTABLE_ROLES = frozenset(
    {
        "plan",
        "report",
        "graph",
        PROJECT_GRAPH_ROLE,
        REFLECTION_LENS_DOC_ROLE,
        "reflection_doc",
        "change_spec",
        "result",
    }
)


def artifact_byte_cap(role: str) -> int | None:
    """Upload byte cap for a submittable role; None = role is not size-capped."""
    if role == "result":
        return METRIC_RESULT_MAX_BYTES
    return GATED_ROLE_BYTE_CAPS.get(role)


# Gated roles: the artifacts workflow gates lint. Submitting one of these pins
# the file's bytes in the blob store (size-capped), so gates and reviewers read
# immutable content. Legacy spellings stay listed so backfilled rows keep
# counting as gated.
GATED_ROLE_BYTE_CAPS: dict[str, int] = {
    "plan": 16_000,
    "report": 16_000,
    "graph": 16_000,
    PROJECT_GRAPH_ROLE: 16_000,
    REFLECTION_LENS_DOC_ROLE: 16_000,
    "reflection_doc": 16_000,
    "change_spec": 16_000,
    **{legacy: 16_000 for legacy in LEGACY_ROLE_REPLACEMENTS},
}
GATED_ROLES = frozenset(GATED_ROLE_BYTE_CAPS)

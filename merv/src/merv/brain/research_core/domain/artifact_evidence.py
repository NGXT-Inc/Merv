"""Research policy and response shaping for Artifact evidence facts.

"Which artifacts count?" has exactly three answers, and they are three named
selectors here: current_slot_artifacts (this attempt, now),
sealed_submission_artifacts (one frozen round), historical_latest_artifacts
(everything the target ever submitted). Every caller picks one; nothing in
this module silently falls back from one meaning to another, because that is
how a rejected prior attempt's graph came to be presented as current.
"""

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


def artifact_slot_key(artifact: dict[str, Any]) -> tuple[str, str, str]:
    """The slot an artifact occupies, within a fixed target and attempt.

    MUST mirror _supersede_slot's key (artifacts/submissions.py) minus the
    target and attempt, which are already fixed by the caller. Kept in lockstep
    by tests/state/test_submission_attempts.py."""
    return (
        str(artifact.get("role") or ""),
        str(artifact.get("lens_id") or ""),
        str(artifact.get("path") or ""),
    )


def latest_per_slot(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest artifact per slot, input order preserved.

    Sealed rounds leave older rows alive (that is the point — a rejected
    round's report stays retrievable), so "what is current" is the newest row
    per slot rather than every row. On data written before submissions existed
    this is a no-op: supersede deleted every older row, so there has only ever
    been one row per slot and this selects the identical set — which is what
    keeps the byte-stable review snapshot from moving."""
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for artifact in artifacts:
        key = artifact_slot_key(artifact)
        held = best.get(key)
        if held is None or artifact_submission_recency_key(
            artifact
        ) > artifact_submission_recency_key(held):
            best[key] = artifact
    keep = {id(artifact) for artifact in best.values()}
    return [artifact for artifact in artifacts if id(artifact) in keep]


def current_slot_artifacts(
    artifacts: list[dict[str, Any]], *, attempt: Any
) -> list[dict[str, Any]]:
    """What the target holds NOW: newest row per slot inside one attempt."""
    return latest_per_slot(
        [
            artifact
            for artifact in artifacts
            if artifact.get("attempt_index") == attempt
        ]
    )


def sealed_submission_artifacts(
    artifacts: list[dict[str, Any]], *, submission_id: str
) -> list[dict[str, Any]]:
    """Exactly the rows one sealed round froze — nothing newer, nothing older.

    An unsealed round has no id, so it selects nothing rather than quietly
    widening to the live composition."""
    if not submission_id:
        return []
    return [
        artifact
        for artifact in artifacts
        if str(artifact.get("submission_id") or "") == submission_id
    ]


def historical_latest_artifacts(
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Newest row per slot across EVERY attempt: history, never "current"."""
    return latest_per_slot(artifacts)


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
        "submission_id": evidence.submission_id,
    }


def preferred_artifact(
    *,
    artifacts: list[dict[str, Any]],
    roles: tuple[str, ...],
) -> dict[str, Any] | None:
    """Highest-precedence role, newest submission, within an already-picked scope.

    Takes no attempt on purpose: the caller has already chosen its scope with
    one of the selectors above, so nothing here can promote a prior attempt's
    artifact into an answer the caller will read as current."""
    role_rank = {role: index for index, role in enumerate(roles)}
    candidates = [
        artifact
        for artifact in artifacts
        if str(artifact.get("role") or "") in role_rank
    ]
    if not candidates:
        return None
    preferred_rank = min(
        role_rank[str(artifact.get("role") or "")] for artifact in candidates
    )
    return max(
        (
            artifact
            for artifact in candidates
            if role_rank[str(artifact.get("role") or "")] == preferred_rank
        ),
        key=artifact_submission_recency_key,
    )


__all__ = [
    "artifact_slot_key",
    "artifact_state_record",
    "artifact_submission_recency_key",
    "current_slot_artifacts",
    "historical_latest_artifacts",
    "latest_per_slot",
    "preferred_artifact",
    "sealed_submission_artifacts",
]

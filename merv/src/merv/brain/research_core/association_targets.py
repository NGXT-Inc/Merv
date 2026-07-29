"""Research-core resolution of artifact-association targets.

Injected into the artifacts module at composition so artifacts never names
research-core tables (Research reaches Artifacts through ports only).
"""

from __future__ import annotations

from ..artifacts import ArtifactTarget
from ..kernel.utils import NotFoundError, ValidationError

_TABLE_BY_TYPE = {
    "experiment": "experiments",
    "reflection": "reflections",
    "claim": "claims",
    "review": "reviews",
}
# Experiments and reflections scope associations to their current attempt, so
# a review rejection that bumps the attempt naturally invalidates stale
# associations for either target kind.
_ATTEMPT_TABLE_BY_TYPE = {"experiment": "experiments", "reflection": "reflections"}
# A published wave is frozen — its pinned graph is the project's comparison
# base — and an abandoned one is closed; neither accepts new artifacts.
_TERMINAL_REFLECTION_STATUSES = ("published", "abandoned")

# A terminal experiment will never take another forward transition, so an
# artifact submitted now would stay unsealed forever while still winning
# latest-per-slot. Submitting DURING a review stays legal on purpose: it moves
# the snapshot and invalidates the pinned verdict, which is the designed way to
# correct work under review, and the next transition seals it.
_CLOSED_EXPERIMENT_STATUSES = ("complete", "failed", "abandoned")


class AssociationTargets:
    """Existence and attempt scoping for association targets (RC-owned SQL)."""

    def resolve(self, *, tx, target: ArtifactTarget) -> ArtifactTarget:
        kind, target_id = target.target_type, target.target_id
        if kind == "attempt":
            # Attempts are implicit in v0.0001.
            return ArtifactTarget(kind, target_id, target.project_id)
        table = _TABLE_BY_TYPE.get(kind)
        if table is None:
            raise ValidationError(f"unsupported target type: {kind}")
        attempt = ", attempt_index" if kind in _ATTEMPT_TABLE_BY_TYPE else ""
        status = ", status" if kind in ("reflection", "experiment") else ""
        row = tx.execute(
            f"SELECT project_id{attempt}{status} FROM {table} WHERE id = ?",
            (target_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"{kind} not found: {target_id}")
        project_id = str(row["project_id"])
        if target.project_id is not None and target.project_id != project_id:
            raise NotFoundError(
                f"{kind} not found in project {target.project_id}: {target_id}"
            )
        if (
            kind == "reflection"
            and str(row["status"]) in _TERMINAL_REFLECTION_STATUSES
        ):
            raise ValidationError(
                f"reflection {target_id} is {row['status']} — the wave is "
                "frozen and no longer accepts artifact submissions"
            )
        if (
            kind == "experiment"
            and str(row["status"]) in _CLOSED_EXPERIMENT_STATUSES
        ):
            # An upload accepted while a round is under review (or after the
            # experiment ended) would land unsealed and win latest-per-slot,
            # silently becoming the row the gate and the reviewer read — work
            # nobody asked for, attributed to a round that already closed.
            raise ValidationError(
                f"experiment {target_id} is {row['status']} — it is not "
                "accepting artifact submissions right now; wait for the "
                "review verdict, then submit against the next round"
            )
        return ArtifactTarget(
            target_type=kind,
            target_id=target_id,
            project_id=project_id,
            attempt_index=int(row["attempt_index"]) if attempt else 0,
        )

    def is_protected(self, *, tx, artifact_id: str) -> bool:
        """Whether a published reflection froze this artifact as its graph."""
        row = tx.execute(
            """
            SELECT 1 FROM reflections
            WHERE published_graph_version_id = ?
            LIMIT 1
            """,
            (artifact_id,),
        ).fetchone()
        return row is not None

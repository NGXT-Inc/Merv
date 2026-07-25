"""Research-core resolution of artifact-association targets.

Injected into the artifacts module at composition so artifacts never names
research-core tables (Research reaches Artifacts through ports only).
"""

from __future__ import annotations

from contextlib import closing

from ..artifacts.ports import AssociationTarget
from ..kernel.state.store import BaseStateStore
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

    def __init__(self, *, store: BaseStateStore) -> None:
        self.store = store

    def resolve(self, *, target_type: str, target_id: str) -> AssociationTarget:
        if target_type == "attempt":
            # Attempts are implicit in v0.0001.
            return AssociationTarget(project_id=None, attempt_index=0)
        table = _TABLE_BY_TYPE.get(target_type)
        if table is None:
            raise ValidationError(f"unsupported target type: {target_type}")
        attempt = ", attempt_index" if target_type in _ATTEMPT_TABLE_BY_TYPE else ""
        status = ", status" if target_type in ("reflection", "experiment") else ""
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                f"SELECT project_id{attempt}{status} FROM {table} WHERE id = ?",
                (target_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"{target_type} not found: {target_id}")
        if target_type == "reflection" and str(row["status"]) in _TERMINAL_REFLECTION_STATUSES:
            raise ValidationError(
                f"reflection {target_id} is {row['status']} — the wave is "
                "frozen and no longer accepts artifact submissions"
            )
        if target_type == "experiment" and str(row["status"]) in _CLOSED_EXPERIMENT_STATUSES:
            # An upload accepted while a round is under review (or after the
            # experiment ended) would land unsealed and win latest-per-slot,
            # silently becoming the row the gate and the reviewer read — work
            # nobody asked for, attributed to a round that already closed.
            raise ValidationError(
                f"experiment {target_id} is {row['status']} — it is not "
                "accepting artifact submissions right now; wait for the "
                "review verdict, then submit against the next round"
            )
        return AssociationTarget(
            project_id=str(row["project_id"]),
            attempt_index=int(row["attempt_index"]) if attempt else 0,
        )

    def publish_pinned_artifact_ids(self, *, conn) -> frozenset[str]:
        """Artifact ids a published reflection froze as its graph pin."""
        rows = conn.execute(
            "SELECT published_graph_version_id FROM reflections "
            "WHERE COALESCE(published_graph_version_id, '') != ''"
        ).fetchall()
        return frozenset(str(row["published_graph_version_id"]) for row in rows)

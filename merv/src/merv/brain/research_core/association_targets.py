"""Research-core resolution of resource-association targets.

Injected into the artifacts module at composition so artifacts never names
research-core tables (import law allows research_core -> artifacts only).
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
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                f"SELECT project_id{attempt} FROM {table} WHERE id = ?", (target_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"{target_type} not found: {target_id}")
        return AssociationTarget(
            project_id=str(row["project_id"]),
            attempt_index=int(row["attempt_index"]) if attempt else 0,
        )

"""Pure application policy for experiment tracking presentation and runs."""

from __future__ import annotations

from typing import Final


# Namespace prefix for every Merv-created tracking experiment.  Pre-rename
# servers hold ``rp/...`` names; the deploy migration renames them in place.
MLFLOW_NAMESPACE_PREFIX: Final = "merv"


def mlflow_experiment_name(*, project_id: str, experiment_id: str) -> str:
    """Stable external-tracking namespace for one Merv experiment."""
    return f"{MLFLOW_NAMESPACE_PREFIX}/{project_id}/{experiment_id}"


MLFLOW_STATE_STATUSES: Final = frozenset(
    {"running", "experiment_review", "complete", "failed"}
)
MLFLOW_TERMINAL_RUN_STATUSES: Final = frozenset(
    {"FINISHED", "FAILED", "KILLED"}
)


def mlflow_visible_for_status(status: object) -> bool:
    """Whether experiment state should carry the tracking context block."""
    return str(status or "") in MLFLOW_STATE_STATUSES


__all__ = [
    "MLFLOW_NAMESPACE_PREFIX",
    "MLFLOW_STATE_STATUSES",
    "MLFLOW_TERMINAL_RUN_STATUSES",
    "mlflow_experiment_name",
    "mlflow_visible_for_status",
]

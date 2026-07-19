"""Pure path-name helpers shared by the brain and stdio proxy."""

from __future__ import annotations


def safe_experiment_dirname(experiment_id: str) -> str:
    """Return a filesystem-safe experiment directory name."""
    return (
        "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in experiment_id)
        or "experiment"
    )

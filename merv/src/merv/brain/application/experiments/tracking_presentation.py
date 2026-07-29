"""Product-neutral tracking context presentation for experiment commands."""

from __future__ import annotations

import logging
from typing import Any

from ...research_core import EXPERIMENT_WORKFLOW
from ..ports.tracking import ExperimentTracking


LOGGER = logging.getLogger(__name__)

_TRACKING_VISIBLE_STATUSES = frozenset().union(
    *(
        EXPERIMENT_WORKFLOW.effect_destinations(effect)
        for effect in (
            "start_tracking",
            "restart_tracking",
            "finish_tracking",
            "fail_tracking",
        )
    )
)

_PRESENTATION_REPAIR = (
    "The state change is committed; only the MLflow context block failed to "
    "assemble, so this response carries no mlflow environment. Do not retry "
    "the call — read mlflow.context for the logging environment."
)


def tracking_failure_message(exc: BaseException) -> str:
    """One caller-actionable line for a tracking failure of any kind."""
    return str(exc).strip() or exc.__class__.__name__


def tracking_warning(*, error: str, repair: str) -> dict[str, str]:
    """The one shape a degraded-tracking warning takes in any response."""
    return {"tracking": "unavailable", "error": error, "repair": repair}


def tracking_visible_for_status(status: object) -> bool:
    """Whether experiment state should carry the tracking context block."""
    return str(status or "") in _TRACKING_VISIBLE_STATUSES


def tracking_connection(
    *,
    tracking: ExperimentTracking | None,
    project_id: str,
    experiment_id: str,
    include_credentials: bool,
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    block = (
        {"configured": False}
        if tracking is None or not project_id or not experiment_id
        else dict(
            tracking.context(
                project_id=project_id,
                experiment_id=experiment_id,
                include_credentials=include_credentials,
            ).to_dict()
        )
    )
    if not run:
        return block
    result = {**block, "run": run}
    run_id = str(run.get("run_id") or "")
    if run_id:
        result["env"] = {
            **dict(result.get("env") or {}),
            "MLFLOW_RUN_ID": run_id,
            "RP_MLFLOW_RUN_ID": run_id,
        }
    return result


def tracking_guidance(block: dict[str, Any]) -> str:
    if not block.get("configured"):
        return str(block.get("note") or "").strip() or (
            "If you run a quantitative experiment, log it to MLflow — but no "
            "central MLflow tracking URI is configured on this backend yet."
        )
    if block.get("experiment_name"):
        return (
            "For this quantitative experiment, set the variables in mlflow.env "
            "(MLFLOW_TRACKING_URI, MLFLOW_EXPERIMENT_NAME, …), then log params, "
            "metrics, artifacts, and required tags to the centralized server. "
            "Use MLflow's native APIs for reads and comparisons."
        )
    return (
        "Use MLflow's native APIs with mlflow.env.MLFLOW_TRACKING_URI to browse "
        "quantitative runs. Search experiment names under "
        f"{block.get('experiment_namespace_prefix') or 'the project namespace'} "
        "or use mlflow.experiments as the plugin experiment-to-MLflow-name map."
    )


def tracking_context_response(
    *, project_id: str, experiment_id: str | None, tracking: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "project_id": project_id,
        "scope": "experiment" if experiment_id else "project",
        "mlflow": tracking,
        "guidance": tracking_guidance(tracking),
    }
    if experiment_id:
        result["experiment_id"] = experiment_id
    return result


def with_tracking_if_visible(
    *,
    state: dict[str, Any],
    tracking: ExperimentTracking | None,
    project_id: str,
    experiment_id: str,
    include_credentials: bool,
) -> dict[str, Any]:
    if tracking is None or not tracking_visible_for_status(state.get("status")):
        return state
    block = tracking_connection(
        tracking=tracking,
        project_id=project_id,
        experiment_id=experiment_id,
        include_credentials=include_credentials,
        run=state.get("mlflow_run"),
    )
    state["mlflow"] = block
    state["mlflow_guidance"] = tracking_guidance(block)
    return state


def attach_tracking_if_visible(
    *,
    state: dict[str, Any],
    tracking: ExperimentTracking | None,
    project_id: str,
    experiment_id: str,
    include_credentials: bool,
) -> dict[str, str] | None:
    """Attach the context block post-commit, degrading a failure to a warning."""
    try:
        with_tracking_if_visible(
            state=state,
            tracking=tracking,
            project_id=project_id,
            experiment_id=experiment_id,
            include_credentials=include_credentials,
        )
    except Exception as exc:  # noqa: BLE001 - a committed transition never fails here
        error = tracking_failure_message(exc)
        LOGGER.error(
            "MLflow context presentation failed for experiment %s: %s",
            experiment_id, error,
        )
        state.pop("mlflow", None)
        state.pop("mlflow_guidance", None)
        return tracking_warning(error=error, repair=_PRESENTATION_REPAIR)
    return None


__all__ = [
    "attach_tracking_if_visible",
    "tracking_connection",
    "tracking_context_response",
    "tracking_failure_message",
    "tracking_guidance",
    "tracking_visible_for_status",
    "tracking_warning",
    "with_tracking_if_visible",
]

"""Product-neutral tracking context presentation for experiment commands."""

from __future__ import annotations

from typing import Any

from ..ports.tracking import ExperimentTracking


def tracking_visible_for_status(status: object) -> bool:
    """Whether experiment state should carry the tracking context block."""
    return str(status or "") in (
        "running", "experiment_review", "complete", "failed"
    )


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
    if not tracking_visible_for_status(state.get("status")):
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


__all__ = [
    "tracking_connection",
    "tracking_context_response",
    "tracking_guidance",
    "tracking_visible_for_status",
    "with_tracking_if_visible",
]

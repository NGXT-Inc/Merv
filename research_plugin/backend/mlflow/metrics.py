"""Compact MLflow REST snapshotting for result read models."""

from __future__ import annotations

import math
from typing import Any

import httpx

# Extraction bounds: the archive is a results record, not a full MLflow mirror.
MAX_EXPERIMENTS = 20
MAX_RUNS = 50
MAX_METRIC_KEYS = 100
MAX_HISTORY_POINTS = 1000
MAX_EXPERIMENT_SCAN = 1000
REQUEST_TIMEOUT = 3.0


def finite_metric_value(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def downsample_history(
    points: list[list[Any]], limit: int = MAX_HISTORY_POINTS
) -> list[list[Any]]:
    if len(points) <= limit:
        return points
    stride = len(points) / limit
    indexes = sorted(
        {min(len(points) - 1, int(i * stride)) for i in range(limit)}
        | {len(points) - 1}
    )
    return [points[i] for i in indexes]


def snapshot_mlflow(
    base_url: str, *, experiment_name: str = ""
) -> dict[str, Any] | None:
    """Extract experiments -> runs -> params/metrics/history from MLflow."""
    base = (base_url or "").split("#", 1)[0].rstrip("/")
    if not base:
        return None
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            experiments = _search_experiments(
                client, base, experiment_name=experiment_name
            )
            captured: list[dict[str, Any]] = []
            for experiment in experiments[:MAX_EXPERIMENTS]:
                experiment_id = str(experiment.get("experiment_id") or "")
                runs = _search_runs(client, base, experiment_id)
                if not runs:
                    continue
                captured.append(
                    {
                        "experiment_id": experiment_id,
                        "name": experiment.get("name") or "",
                        "last_update_time": experiment.get("last_update_time"),
                        "runs": [_run_record(client, base, run) for run in runs],
                    }
                )
    except Exception:  # noqa: BLE001 - snapshotting is best-effort by contract
        return None
    return {"source": "mlflow", "base_url": base, "experiments": captured} if captured else None


def _search_experiments(
    client: httpx.Client, base: str, *, experiment_name: str = ""
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"max_results": MAX_EXPERIMENT_SCAN}
    if experiment_name:
        params["filter"] = "name = '" + experiment_name.replace("'", "\\'") + "'"
    try:
        response = client.get(f"{base}/api/2.0/mlflow/experiments/search", params=params)
        response.raise_for_status()
    except Exception:
        if not experiment_name:
            raise
        response = client.get(
            f"{base}/api/2.0/mlflow/experiments/search",
            params={"max_results": MAX_EXPERIMENT_SCAN},
        )
        response.raise_for_status()
    experiments = [
        e for e in (response.json().get("experiments") or []) if isinstance(e, dict)
    ]
    if experiment_name:
        experiments = [
            e for e in experiments if str(e.get("name") or "") == experiment_name
        ]
    return experiments


def _search_runs(
    client: httpx.Client, base: str, experiment_id: str
) -> list[dict[str, Any]]:
    if not experiment_id:
        return []
    try:
        response = client.post(
            f"{base}/api/2.0/mlflow/runs/search",
            json={
                "experiment_ids": [experiment_id],
                "order_by": ["attributes.start_time DESC"],
                "max_results": MAX_RUNS,
            },
        )
        if response.status_code != 200:
            return []
        runs = response.json().get("runs") or []
    except Exception:  # noqa: BLE001
        return []
    return [run for run in runs if isinstance(run, dict)][:MAX_RUNS]


def _run_record(client: httpx.Client, base: str, run: dict[str, Any]) -> dict[str, Any]:
    info = run.get("info") or {}
    data = run.get("data") or {}
    run_id = str(info.get("run_id") or "")
    params = {
        str(param.get("key")): param.get("value")
        for param in (data.get("params") or [])
        if isinstance(param, dict) and param.get("key")
    }
    # User tags only: mlflow.* system tags carry no identity the exhibit or
    # readers need, and they double the record size.
    tags = {
        str(tag.get("key")): str(tag.get("value") or "")
        for tag in (data.get("tags") or [])
        if isinstance(tag, dict)
        and tag.get("key")
        and not str(tag["key"]).startswith("mlflow.")
    }
    metrics: dict[str, dict[str, Any]] = {}
    history: dict[str, list[list[Any]]] = {}
    for metric in (data.get("metrics") or [])[:MAX_METRIC_KEYS]:
        if not isinstance(metric, dict) or not metric.get("key"):
            continue
        key = str(metric["key"])
        points = _metric_history(client, base, run_id, key)
        metrics[key] = {
            "last": finite_metric_value(metric.get("value")),
            "step": metric.get("step"),
            "timestamp": metric.get("timestamp"),
        }
        if points:
            history[key] = points
            values = [value for _, value in points if value is not None]
            if values:
                metrics[key]["min"] = min(values)
                metrics[key]["max"] = max(values)
    return {
        "run_id": run_id,
        "run_name": str(info.get("run_name") or ""),
        "status": str(info.get("status") or ""),
        "start_time": info.get("start_time"),
        "end_time": info.get("end_time"),
        "params": params,
        "tags": tags,
        "metrics": metrics,
        "history": history,
    }


def _metric_history(
    client: httpx.Client, base: str, run_id: str, key: str
) -> list[list[Any]]:
    if not run_id:
        return []
    try:
        response = client.get(
            f"{base}/api/2.0/mlflow/metrics/get-history",
            params={"run_id": run_id, "metric_key": key},
        )
        if response.status_code != 200:
            return []
        raw = response.json().get("metrics") or []
    except Exception:  # noqa: BLE001
        return []
    return downsample_history(
        [
            [metric.get("step") or 0, finite_metric_value(metric.get("value"))]
            for metric in raw
            if isinstance(metric, dict)
        ]
    )

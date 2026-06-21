"""Persist MLflow metrics so experiment results outlive the sandbox VM.

The MLflow tracking server runs ON the sandbox; releasing the VM would take
the entire metrics history with it. The daemon therefore extracts a structured
snapshot through MLflow's REST API — over the dashboard tunnel it already owns
— and writes it to a daemon-owned JSON file per experiment. Snapshots are
taken on every sync (throttled in the auto-sync loop) and force-refreshed
right before release/reap; afterwards the archive is the durable record the
HTTP API serves from.

Snapshot shape::

    {
      "captured_at": "<iso8601>",
      "source": "mlflow",
      "base_url": "http://127.0.0.1:<port>",
      "experiments": [
        {"experiment_id", "name", "last_update_time",
         "runs": [
            {"run_id", "run_name", "status", "start_time", "end_time",
             "params": {key: value},
             "metrics": {key: {"last", "step", "timestamp", "min", "max"}},
             "history": {key: [[step, value], ...]}}  # downsampled
         ]}
      ]
    }
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ..utils import format_iso

# Extraction bounds: the archive is a results record, not a full MLflow
# mirror. History is downsampled; final values are always exact.
MAX_EXPERIMENTS = 20
MAX_RUNS = 50
MAX_METRIC_KEYS = 100
MAX_HISTORY_POINTS = 1000
REQUEST_TIMEOUT = 3.0


def _finite(value: Any) -> float | None:
    # MLflow can log NaN/Infinity, which std json emits as literals that
    # break browser JSON.parse — store them as null instead.
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _downsample(points: list[list[Any]], limit: int = MAX_HISTORY_POINTS) -> list[list[Any]]:
    if len(points) <= limit:
        return points
    stride = len(points) / limit
    indexes = sorted(
        {min(len(points) - 1, int(i * stride)) for i in range(limit)} | {len(points) - 1}
    )
    return [points[i] for i in indexes]


def snapshot_mlflow(base_url: str) -> dict[str, Any] | None:
    """Extract experiments → runs → params/metrics/history from one MLflow server.

    Returns ``None`` when the server is unreachable or holds no runs at all, so
    a caller never overwrites a previously captured archive with emptiness.
    Run lists and metric histories are best-effort per item: a failure there
    degrades that item rather than the whole snapshot.
    """
    base = (base_url or "").split("#", 1)[0].rstrip("/")
    if not base:
        return None
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            experiments = _search_experiments(client, base)
            captured: list[dict[str, Any]] = []
            for experiment in experiments[:MAX_EXPERIMENTS]:
                runs = _search_runs(client, base, str(experiment.get("experiment_id") or ""))
                if not runs:
                    continue
                captured.append(
                    {
                        "experiment_id": str(experiment.get("experiment_id") or ""),
                        "name": experiment.get("name") or "",
                        "last_update_time": experiment.get("last_update_time"),
                        "runs": [_run_record(client, base, run) for run in runs[:MAX_RUNS]],
                    }
                )
    except Exception:  # noqa: BLE001 — snapshotting is best-effort by contract
        return None
    if not captured:
        return None
    return {"source": "mlflow", "base_url": base, "experiments": captured}


def _search_experiments(client: httpx.Client, base: str) -> list[dict[str, Any]]:
    response = client.get(
        f"{base}/api/2.0/mlflow/experiments/search", params={"max_results": 200}
    )
    response.raise_for_status()
    experiments = response.json().get("experiments") or []
    return [e for e in experiments if isinstance(e, dict)]


def _search_runs(client: httpx.Client, base: str, experiment_id: str) -> list[dict[str, Any]]:
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
    return [r for r in runs if isinstance(r, dict)]


def _run_record(client: httpx.Client, base: str, run: dict[str, Any]) -> dict[str, Any]:
    info = run.get("info") or {}
    data = run.get("data") or {}
    run_id = str(info.get("run_id") or "")
    params = {
        str(p.get("key")): p.get("value")
        for p in (data.get("params") or [])
        if isinstance(p, dict) and p.get("key")
    }
    metrics: dict[str, dict[str, Any]] = {}
    history: dict[str, list[list[Any]]] = {}
    for metric in (data.get("metrics") or [])[:MAX_METRIC_KEYS]:
        if not isinstance(metric, dict) or not metric.get("key"):
            continue
        key = str(metric["key"])
        metrics[key] = {
            "last": _finite(metric.get("value")),
            "step": metric.get("step"),
            "timestamp": metric.get("timestamp"),
        }
        points = _metric_history(client, base, run_id, key)
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
    points = [
        [m.get("step") or 0, _finite(m.get("value"))] for m in raw if isinstance(m, dict)
    ]
    return _downsample(points)


def snapshot_mlflow_db(db_path: Path) -> dict[str, Any] | None:
    """Extract the same snapshot shape from a pulled ``mlflow.db`` SQLite file.

    Fallback for when MLflow's REST API is unreachable: the sandbox's MLflow
    backend store lives inside the synced workspace, so the rsync pull usually
    captured it even when the server (or the whole VM) is already gone. Also
    the rescue path for sandboxes terminated before REST archiving existed.
    """
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        conn.row_factory = sqlite3.Row
        experiments = conn.execute(
            "SELECT experiment_id, name, IFNULL(last_update_time, 0) AS last_update_time"
            " FROM experiments WHERE lifecycle_stage != 'deleted'"
            f" ORDER BY last_update_time DESC LIMIT {MAX_EXPERIMENTS}"
        ).fetchall()
        captured: list[dict[str, Any]] = []
        for experiment in experiments:
            runs = conn.execute(
                "SELECT run_uuid, name, status, start_time, end_time FROM runs"
                " WHERE experiment_id = ? AND lifecycle_stage != 'deleted'"
                f" ORDER BY start_time DESC LIMIT {MAX_RUNS}",
                (experiment["experiment_id"],),
            ).fetchall()
            if not runs:
                continue
            captured.append(
                {
                    "experiment_id": str(experiment["experiment_id"]),
                    "name": experiment["name"] or "",
                    "last_update_time": experiment["last_update_time"],
                    "runs": [_run_record_from_db(conn, run) for run in runs],
                }
            )
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not captured:
        return None
    return {"source": "mlflow", "extracted_from": str(db_path), "experiments": captured}


def _run_record_from_db(conn: sqlite3.Connection, run: sqlite3.Row) -> dict[str, Any]:
    run_id = str(run["run_uuid"])
    params = {
        str(row["key"]): row["value"]
        for row in conn.execute("SELECT key, value FROM params WHERE run_uuid = ?", (run_id,))
    }
    metrics: dict[str, dict[str, Any]] = {}
    history: dict[str, list[list[Any]]] = {}
    latest = conn.execute(
        "SELECT key, value, timestamp, step, is_nan FROM latest_metrics"
        f" WHERE run_uuid = ? LIMIT {MAX_METRIC_KEYS}",
        (run_id,),
    ).fetchall()
    for row in latest:
        key = str(row["key"])
        metrics[key] = {
            "last": None if row["is_nan"] else _finite(row["value"]),
            "step": row["step"],
            "timestamp": row["timestamp"],
        }
        points = [
            [r["step"] or 0, None if r["is_nan"] else _finite(r["value"])]
            for r in conn.execute(
                "SELECT value, step, is_nan FROM metrics"
                " WHERE run_uuid = ? AND key = ? ORDER BY timestamp, step",
                (run_id, key),
            )
        ]
        points = _downsample(points)
        if points:
            history[key] = points
            values = [value for _, value in points if value is not None]
            if values:
                metrics[key]["min"] = min(values)
                metrics[key]["max"] = max(values)
    return {
        "run_id": run_id,
        "run_name": str(run["name"] or ""),
        "status": str(run["status"] or ""),
        "start_time": run["start_time"],
        "end_time": run["end_time"],
        "params": params,
        "metrics": metrics,
        "history": history,
    }


class MetricsArchive:
    """Daemon-owned per-experiment metrics snapshots on local disk.

    Lives beside the sandbox keys under ``.research_plugin/sandboxes/`` so the
    archive shares the project's lifetime, not the VM's.
    """

    def __init__(self, *, repo_root: Path) -> None:
        self.root = Path(repo_root) / ".research_plugin" / "sandboxes" / "metrics"

    def path_for(self, experiment_id: str) -> Path:
        return self.root / f"{experiment_id}.json"

    def persist(self, *, experiment_id: str, snapshot: dict[str, Any]) -> Path:
        record = {"captured_at": format_iso(datetime.now(tz=UTC)), **snapshot}
        path = self.path_for(experiment_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{experiment_id}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
        return path

    def load(self, *, experiment_id: str) -> dict[str, Any] | None:
        try:
            with self.path_for(experiment_id).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

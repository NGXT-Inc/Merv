"""Persist MLflow metrics so experiment results remain queryable.

New runs use a centralized, backend-owned MLflow tracking server. The control
plane extracts a compact structured snapshot for the one Research Plugin
experiment and writes it to the durable metrics record. The pulled ``mlflow.db``
reader below remains as a legacy fallback for older sandbox-local runs and
partially rescued sandboxes.

Snapshot shape::

    {
      "captured_at": "<iso8601>",
      "source": "mlflow",
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
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..mlflow_metrics import (
    MAX_EXPERIMENTS,
    MAX_HISTORY_POINTS,
    MAX_METRIC_KEYS,
    MAX_RUNS,
    downsample_history,
    finite_metric_value,
)
from ..utils import format_iso

def snapshot_mlflow_db(db_path: Path) -> dict[str, Any] | None:
    """Extract the same snapshot shape from a pulled ``mlflow.db`` SQLite file.

    Legacy fallback for pre-centralization runs: the sandbox's MLflow backend
    store may have been mirrored locally by rsync even when the server (or the
    whole VM) is already gone. Also the rescue path for sandboxes terminated
    before REST archiving existed.
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
            "last": None if row["is_nan"] else finite_metric_value(row["value"]),
            "step": row["step"],
            "timestamp": row["timestamp"],
        }
        points = [
            [r["step"] or 0, None if r["is_nan"] else finite_metric_value(r["value"])]
            for r in conn.execute(
                "SELECT value, step, is_nan FROM metrics"
                " WHERE run_uuid = ? AND key = ? ORDER BY timestamp, step",
                (run_id, key),
            )
        ]
        points = downsample_history(points)
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

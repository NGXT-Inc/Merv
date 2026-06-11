from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.metrics_archive import (
    MAX_HISTORY_POINTS,
    MetricsArchive,
    _downsample,
    snapshot_mlflow,
    snapshot_mlflow_db,
)


def write_fake_mlflow_db(path: Path, *, with_run: bool = True) -> None:
    """Minimal slice of MLflow's SQLAlchemy schema (verified against 2.18)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE experiments (
            experiment_id INTEGER PRIMARY KEY, name TEXT,
            lifecycle_stage TEXT DEFAULT 'active', last_update_time BIGINT
        );
        CREATE TABLE runs (
            run_uuid TEXT PRIMARY KEY, name TEXT, status TEXT,
            start_time BIGINT, end_time BIGINT,
            lifecycle_stage TEXT DEFAULT 'active', experiment_id INTEGER
        );
        CREATE TABLE params (key TEXT, value TEXT, run_uuid TEXT);
        CREATE TABLE latest_metrics (
            key TEXT, value FLOAT, timestamp BIGINT, step BIGINT,
            is_nan BOOLEAN, run_uuid TEXT
        );
        CREATE TABLE metrics (
            key TEXT, value FLOAT, timestamp BIGINT, run_uuid TEXT,
            step BIGINT DEFAULT 0, is_nan BOOLEAN DEFAULT 0
        );
        """
    )
    conn.execute("INSERT INTO experiments VALUES (0, 'Default', 'active', 1)")
    conn.execute("INSERT INTO experiments VALUES (1, 'lora_glue', 'active', 99)")
    if with_run:
        conn.execute(
            "INSERT INTO runs VALUES ('r1', 'seed_0', 'FINISHED', 100, 200, 'active', 1)"
        )
        conn.execute("INSERT INTO params VALUES ('lr', '0.0005', 'r1')")
        conn.execute("INSERT INTO latest_metrics VALUES ('acc', 0.91, 6, 20, 0, 'r1')")
        conn.execute("INSERT INTO metrics VALUES ('acc', 0.85, 5, 'r1', 10, 0)")
        conn.execute("INSERT INTO metrics VALUES ('acc', 0.91, 6, 'r1', 20, 0)")
    conn.commit()
    conn.close()


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class FakeClient:
    """Routes MLflow REST paths to canned payloads; no network."""

    def __init__(self, *, experiments=None, runs=None, history=None, fail=False) -> None:
        self.experiments = experiments or []
        self.runs = runs or {}
        self.history = history or {}
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None):
        if self.fail:
            raise OSError("connection refused")
        if url.endswith("/experiments/search"):
            return FakeResponse({"experiments": self.experiments})
        if url.endswith("/metrics/get-history"):
            key = (params or {}).get("run_id"), (params or {}).get("metric_key")
            return FakeResponse({"metrics": self.history.get(key, [])})
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, json=None):
        if self.fail:
            raise OSError("connection refused")
        if url.endswith("/runs/search"):
            experiment_id = (json or {}).get("experiment_ids", [""])[0]
            return FakeResponse({"runs": self.runs.get(experiment_id, [])})
        raise AssertionError(f"unexpected POST {url}")


def _client_patch(client: FakeClient):
    return patch(
        "backend.services.metrics_archive.httpx.Client",
        return_value=client,
    )


class SnapshotMlflowTest(unittest.TestCase):
    def test_snapshot_captures_runs_params_metrics_history(self) -> None:
        client = FakeClient(
            experiments=[
                {"experiment_id": "0", "name": "Default", "last_update_time": 1},
                {"experiment_id": "1", "name": "lora_glue", "last_update_time": 99},
            ],
            runs={
                # Default has no runs and must be skipped entirely.
                "0": [],
                "1": [
                    {
                        "info": {
                            "run_id": "r1",
                            "run_name": "seed_0",
                            "status": "FINISHED",
                            "start_time": 100,
                            "end_time": 200,
                        },
                        "data": {
                            "params": [{"key": "lr", "value": "0.0005"}],
                            "metrics": [
                                {"key": "acc", "value": 0.91, "step": 20, "timestamp": 5},
                                # Non-finite final value must be stored as null,
                                # not break JSON for browsers.
                                {"key": "bad", "value": float("nan"), "step": 1, "timestamp": 5},
                            ],
                        },
                    }
                ],
            },
            history={
                ("r1", "acc"): [
                    {"step": 10, "value": 0.85},
                    {"step": 20, "value": 0.91},
                ],
                ("r1", "bad"): [],
            },
        )
        with _client_patch(client):
            snapshot = snapshot_mlflow("http://127.0.0.1:5000/#/experiments/1")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["base_url"], "http://127.0.0.1:5000")
        self.assertEqual(len(snapshot["experiments"]), 1)
        exp = snapshot["experiments"][0]
        self.assertEqual(exp["name"], "lora_glue")
        run = exp["runs"][0]
        self.assertEqual(run["run_name"], "seed_0")
        self.assertEqual(run["params"], {"lr": "0.0005"})
        self.assertEqual(run["metrics"]["acc"]["last"], 0.91)
        self.assertEqual(run["metrics"]["acc"]["min"], 0.85)
        self.assertEqual(run["metrics"]["acc"]["max"], 0.91)
        self.assertEqual(run["history"]["acc"], [[10, 0.85], [20, 0.91]])
        self.assertIsNone(run["metrics"]["bad"]["last"])
        self.assertNotIn("bad", run["history"])
        # The whole record must be strict JSON (no NaN literals).
        json.loads(json.dumps(snapshot, allow_nan=False))

    def test_snapshot_none_when_no_runs_anywhere(self) -> None:
        client = FakeClient(
            experiments=[{"experiment_id": "0", "name": "Default", "last_update_time": 1}],
            runs={"0": []},
        )
        with _client_patch(client):
            self.assertIsNone(snapshot_mlflow("http://x"))

    def test_snapshot_none_when_unreachable_or_blank(self) -> None:
        with _client_patch(FakeClient(fail=True)):
            self.assertIsNone(snapshot_mlflow("http://x"))
        self.assertIsNone(snapshot_mlflow(""))

    def test_downsample_caps_points_and_keeps_endpoints(self) -> None:
        points = [[i, float(i)] for i in range(5000)]
        sampled = _downsample(points)
        self.assertLessEqual(len(sampled), MAX_HISTORY_POINTS + 1)
        self.assertEqual(sampled[0], [0, 0.0])
        self.assertEqual(sampled[-1], [4999, 4999.0])
        short = [[0, 1.0], [1, 2.0]]
        self.assertEqual(_downsample(short), short)


class SnapshotMlflowDbTest(unittest.TestCase):
    def test_extracts_runs_from_pulled_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mlflow.db"
            write_fake_mlflow_db(db)
            snapshot = snapshot_mlflow_db(db)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["extracted_from"], str(db))
        # Default has no runs and is skipped — same rule as the REST path.
        self.assertEqual(len(snapshot["experiments"]), 1)
        run = snapshot["experiments"][0]["runs"][0]
        self.assertEqual(run["run_name"], "seed_0")
        self.assertEqual(run["status"], "FINISHED")
        self.assertEqual(run["params"], {"lr": "0.0005"})
        self.assertEqual(run["metrics"]["acc"]["last"], 0.91)
        self.assertEqual(run["metrics"]["acc"]["min"], 0.85)
        self.assertEqual(run["history"]["acc"], [[10, 0.85], [20, 0.91]])
        json.loads(json.dumps(snapshot, allow_nan=False))

    def test_none_for_missing_or_runless_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(snapshot_mlflow_db(Path(tmp) / "absent.db"))
            db = Path(tmp) / "empty.db"
            write_fake_mlflow_db(db, with_run=False)
            self.assertIsNone(snapshot_mlflow_db(db))
            corrupt = Path(tmp) / "corrupt.db"
            corrupt.write_text("not a sqlite file", encoding="utf-8")
            self.assertIsNone(snapshot_mlflow_db(corrupt))


class MetricsArchiveTest(unittest.TestCase):
    def test_persist_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = MetricsArchive(repo_root=Path(tmp))
            snapshot = {"source": "mlflow", "experiments": [{"experiment_id": "1"}]}
            path = archive.persist(experiment_id="exp_abc", snapshot=snapshot)
            self.assertTrue(path.exists())
            self.assertIn(".research_plugin", str(path))
            loaded = archive.load(experiment_id="exp_abc")
            self.assertEqual(loaded["experiments"], snapshot["experiments"])
            self.assertIn("captured_at", loaded)
            # No leftover temp files from the atomic write.
            leftovers = [p for p in path.parent.iterdir() if p.suffix == ".tmp"]
            self.assertEqual(leftovers, [])

    def test_load_missing_or_corrupt_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = MetricsArchive(repo_root=Path(tmp))
            self.assertIsNone(archive.load(experiment_id="exp_none"))
            path = archive.path_for("exp_bad")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(archive.load(experiment_id="exp_bad"))


if __name__ == "__main__":
    unittest.main()

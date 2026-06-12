"""MLflow metrics snapshots as control-plane records (cloud plan Phase 5).

The daemon keeps the per-experiment JSON file cache it always had
(``MetricsArchive``); this store additionally lands every snapshot in the
record layer (``metrics_snapshots``), so reviews and the UI read metrics
without the user's machine online — "daemon offline at reap" no longer
means "no metrics". One row per experiment: the latest snapshot, mirroring
the file cache's overwrite semantics. Records only — no local IO — so the
same code serves from a cloud control plane.
"""

from __future__ import annotations

import json
from typing import Any

from ..state.store import StateStore
from ..utils import now_iso


class MetricsSnapshotStore:
    """Latest-per-experiment metrics snapshot records."""

    def __init__(self, *, store: StateStore) -> None:
        self.store = store

    def record(
        self, *, experiment_id: str, project_id: str, snapshot: dict[str, Any]
    ) -> None:
        """Upsert the experiment's snapshot record.

        ``snapshot`` is the extracted MLflow shape (see metrics_archive);
        the stored record gains ``captured_at`` exactly like the file cache
        does at persist time, so both read paths serve the same shape.
        """
        captured_at = str(snapshot.get("captured_at") or now_iso())
        record = {"captured_at": captured_at, **snapshot}
        with self.store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO metrics_snapshots
                  (experiment_id, project_id, captured_at, source, snapshot_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(experiment_id) DO UPDATE SET
                  project_id = excluded.project_id,
                  captured_at = excluded.captured_at,
                  source = excluded.source,
                  snapshot_json = excluded.snapshot_json
                """,
                (
                    experiment_id,
                    project_id,
                    captured_at,
                    str(snapshot.get("source") or ""),
                    json.dumps(record, ensure_ascii=False, sort_keys=True),
                ),
            )

    def load(self, *, experiment_id: str) -> dict[str, Any] | None:
        """The experiment's snapshot record, in the file cache's read shape."""
        conn = self.store.connect()
        try:
            row = conn.execute(
                "SELECT snapshot_json FROM metrics_snapshots WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        try:
            data = json.loads(str(row["snapshot_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

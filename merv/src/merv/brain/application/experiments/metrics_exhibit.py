"""Pure construction of a system-authored metrics exhibit.

The exhibit is the observation-not-attestation record of a quantitative
attempt: a bounded tracking snapshot filtered to the current attempt window
plus eligible pinned result-file sources, each with provenance.  Callers
supply plain data, so preview and final pinning use identical construction.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ...kernel.utils import format_iso, parse_iso
from ..ports.tracking import MAX_TRACKING_SNAPSHOT_RUNS, MetricsSnapshot

METRICS_EXHIBIT_KIND = "metrics_exhibit"
METRICS_EXHIBIT_FILENAME = "metrics_exhibit.json"
WINDOW_SKEW_MS = 5 * 60 * 1000


def iso_to_epoch_ms(value: Any) -> int | None:
    parsed = parse_iso(value)
    return None if parsed is None else int(parsed.timestamp() * 1000)


def _epoch_ms_to_iso(value: Any) -> str:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return ""
    return format_iso(datetime.fromtimestamp(millis / 1000, tz=timezone.utc))


def _exhibit_run(*, run: dict[str, Any]) -> dict[str, Any]:
    metrics = {
        key: {k: v for k, v in value.items() if k in ("last", "step", "min", "max")}
        for key, value in (run.get("metrics") or {}).items()
        if isinstance(value, dict)
    }
    exhibit_run = {
        "run_id": run.get("run_id") or "",
        "run_name": run.get("run_name") or "",
        "status": run.get("status") or "",
        "started_at": _epoch_ms_to_iso(run.get("start_time")),
        "ended_at": _epoch_ms_to_iso(run.get("end_time")),
        "params": run.get("params") or {},
        "tags": run.get("tags") or {},
        "metrics": metrics,
        "source": {"type": "mlflow", "run_id": run.get("run_id") or ""},
    }
    if run.get("metrics_capped_at"):
        exhibit_run["metrics_capped_at"] = run["metrics_capped_at"]
    return exhibit_run


def _snapshot_runs(
    snapshot: MetricsSnapshot | None, *, experiment_name: str
) -> tuple[list[dict[str, Any]], bool]:
    """Return runs and availability for the requested tracking namespace."""
    if not isinstance(snapshot, dict) or not snapshot.get("available"):
        return [], False
    for experiment in snapshot.get("experiments") or []:
        if str(experiment.get("name") or "") == experiment_name:
            return [
                run
                for run in experiment.get("runs") or []
                if isinstance(run, dict)
            ], True
    return [], True


def build_metrics_exhibit(
    *,
    project_id: str,
    experiment_id: str,
    attempt_index: int,
    experiment_name: str,
    window_started_at: str | None,
    snapshot: MetricsSnapshot | None,
    mlflow_configured: bool,
    file_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic exhibit document from supplied observations."""
    all_runs, available = _snapshot_runs(snapshot, experiment_name=experiment_name)
    window_started_ms = iso_to_epoch_ms(window_started_at)
    window_floor_ms = (
        window_started_ms - WINDOW_SKEW_MS
        if window_started_ms is not None
        else None
    )
    in_window = [
        run
        for run in all_runs
        if window_floor_ms is None
        or (
            isinstance(run.get("start_time"), (int, float))
            and run["start_time"] >= window_floor_ms
        )
    ]
    in_window.sort(
        key=lambda run: (run.get("start_time") or 0, str(run.get("run_id") or ""))
    )
    runs = [_exhibit_run(run=run) for run in in_window]
    files = [
        {
            "path": source.get("path") or "",
            "data": source.get("data"),
            "source": {
                "type": "result_file",
                "path": source.get("path") or "",
                "artifact_id": source.get("artifact_id") or "",
                "sha256": source.get("sha256") or "",
                "submitted_at": source.get("submitted_at") or "",
            },
        }
        for source in file_sources
    ]
    exhibit: dict[str, Any] = {
        "kind": METRICS_EXHIBIT_KIND,
        "project_id": project_id,
        "experiment_id": experiment_id,
        "attempt_index": int(attempt_index),
        # The generation instant lives out-of-band so identical inputs remain
        # byte-identical between preview and final pinning.
        "window": {"started_at": window_started_at or ""},
        "mlflow": {
            "configured": mlflow_configured,
            "available": available,
            "experiment_name": experiment_name,
            "runs_excluded_by_window": len(all_runs) - len(in_window),
        },
        "runs": runs,
        "result_files": files,
        "verdict": {
            "runs_found": len(runs),
            "result_files": len(files),
        },
    }
    # A full readback page means older attempt runs may be absent.  Make the
    # bound explicit rather than silently claiming an exhaustive record.
    if len(all_runs) >= MAX_TRACKING_SNAPSHOT_RUNS:
        exhibit["mlflow"]["runs_capped_at"] = MAX_TRACKING_SNAPSHOT_RUNS
    return exhibit


def exhibit_bytes(exhibit: dict[str, Any]) -> bytes:
    """Canonical, reproducible bytes used for pinning."""
    return (json.dumps(exhibit, indent=2, sort_keys=True) + "\n").encode("utf-8")


__all__ = [
    "METRICS_EXHIBIT_FILENAME",
    "METRICS_EXHIBIT_KIND",
    "WINDOW_SKEW_MS",
    "build_metrics_exhibit",
    "exhibit_bytes",
    "iso_to_epoch_ms",
]

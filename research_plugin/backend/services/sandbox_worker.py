"""Service-side protocol for sandbox data-plane work."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol


class SandboxWorker(Protocol):
    """Local sandbox duties the services call through the data-plane seam."""

    def set_event_sink(self, emit_event: Callable[..., None]) -> None: ...

    def ensure_local_dashboards(self, *, row: dict[str, Any]) -> dict[str, Any]: ...

    def merge_local_dashboards(self, *, row: dict[str, Any]) -> dict[str, Any]: ...

    def stop_dashboards(self, *, sandbox_id: str = "") -> None: ...

    def repo_relative(self, path: str | Path) -> str: ...

    def capture_metrics_fallback(
        self, *, experiment_id: str, name: str = ""
    ) -> dict[str, Any] | None: ...

    def capture_metrics_snapshot(
        self, *, row: dict[str, Any], name: str = ""
    ) -> dict[str, Any] | None: ...

    def local_experiment_dir(self, *, experiment_id: str, name: str = "") -> Path: ...

    def pulled_mlflow_db_path(self, *, experiment_id: str, name: str = "") -> Path: ...

    def ensure_keypair(self, *, experiment_id: str) -> tuple[str, Path]: ...

    def sandbox_enrichment(
        self, *, row: dict[str, Any], name: str = ""
    ) -> dict[str, Any]: ...

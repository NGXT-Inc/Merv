"""Shared per-target auto-sync step for local and daemon pollers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def run_auto_sync_target(
    *,
    target: Mapping[str, Any],
    sync_pull: Callable[..., dict[str, Any]],
    sync_includes_row: bool = False,
    after_sync: Callable[..., Any] | None = None,
) -> tuple[dict[str, Any], Any | None]:
    """Sync one leased target and run best-effort post-sync work if it pulled."""
    row = dict(target.get("row") or {})
    session = target.get("session")
    if not isinstance(session, dict):
        raise ValueError("sync target session is required")
    sync_kwargs: dict[str, Any] = {"session": session, "skip_if_busy": True}
    if sync_includes_row:
        sync_kwargs["row"] = row
    result = sync_pull(**sync_kwargs)
    after_result = None
    if not result.get("skipped") and after_sync is not None:
        try:
            after_result = after_sync(row=row)
        except Exception:  # noqa: BLE001 - auto-sync post-work is best-effort
            after_result = None
    return result, after_result

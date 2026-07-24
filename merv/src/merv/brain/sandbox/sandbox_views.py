"""Sandbox-row projections shared across the agent surface, workflow, and UI.

These functions turn a raw ``sandboxes`` row into the dicts callers consume:

- ``agent_row_facts`` — the provider-portable response for
  ``sandbox.request``/``sandbox.get``.
- ``sandbox_row_view`` — the canonical row projection used by the workflow's
  agent-facing status AND the HTTP/UI layer (formerly ``_ui_view``).
- ``agent_summary`` — the compact per-row shape for ``sandbox.list``.
- ``needs_selection_view`` — the "pick a machine" response for bundled-hardware
  backends.

They are pure projection logic — no DB, backend, or filesystem calls.
"""

from __future__ import annotations
from typing import Any

from .sandbox_paths import DEFAULT_DATA_DIR, remote_experiment_dir
from .sandbox_support import ACTIVE_SANDBOX_STATUSES, POLL_AFTER_SECONDS


def _sandbox_dirs(*, row: dict[str, Any]) -> tuple[str, str]:
    experiment_id = str(row.get("experiment_id") or "")
    remote_dir = str(
        row.get("sync_dir")
        or row.get("workdir")
        or remote_experiment_dir(
            experiment_id=str(row.get("sandbox_uid") or experiment_id)
        )
    )
    data_dir = str(
        row.get("sandbox_data_dir") or row.get("unsynced_dir") or DEFAULT_DATA_DIR
    )
    return remote_dir, data_dir


def _runtime_hint(
    *,
    status: str,
    ssh: dict[str, Any],
    remote_dir: str,
    expires_at: Any,
    storage_enabled: bool,
) -> str:
    """Brain-composed lifecycle guidance using only durable sandbox facts."""
    expiry = (
        f" This sandbox expires at {expires_at}; retain valuable output before "
        "that deadline or before release."
        if expires_at
        else ""
    )
    retention = (
        " Nothing is copied out automatically. Use sandbox.pull_outputs for "
        "selected light files, then artifact.submit for gated documents"
        + (
            ", and storage.submit for durable heavy artifacts."
            if storage_enabled
            else "; durable heavy-artifact storage is not configured."
        )
    )
    if status == "provisioning":
        return (
            "Provisioning is in progress. Poll sandbox.get after "
            f"{POLL_AFTER_SECONDS} seconds until status is running; do not "
            "re-call sandbox.request as a poll. Once running, connect using "
            "the returned ssh.host, ssh.port, and ssh.user with the "
            "caller-owned private key."
            + expiry
            + retention
        )
    if status == "failed":
        return (
            "Provisioning failed; inspect error, correct the request or "
            "provider issue, then call sandbox.request to retry."
        )
    if (
        status in ACTIVE_SANDBOX_STATUSES
        and ssh.get("host")
        and ssh.get("port")
    ):
        return (
            "Connect using the returned ssh.host, ssh.port, and ssh.user with "
            "the caller-owned private key. Work in the remote experiment_dir "
            f"({remote_dir}); use merv_run for long jobs and sandbox.runs for "
            "their receipts."
            + expiry
            + retention
            + " Call sandbox.get once if the SSH endpoint becomes stale."
        )
    return "No live sandbox found; call sandbox.request to create one."


def agent_row_facts(
    *,
    row: dict[str, Any],
    env_info: dict[str, Any],
    reused: bool | None,
    storage_enabled: bool = False,
) -> dict[str, Any]:
    """Provider-portable agent response built only from durable row facts."""
    status = row.get("status") or "none"
    active_experiment_ids = list(row.get("active_experiment_ids") or [])
    remote_dir, data_dir = _sandbox_dirs(row=row)
    facts: dict[str, Any] = {
        "sandbox_uid": row.get("sandbox_uid"),
        "experiment_id": row.get("experiment_id"),
        "active_experiment_ids": active_experiment_ids,
        "project_id": row.get("project_id"),
        "sandbox_id": row.get("sandbox_id"),
        "status": status,
        "ssh": {
            "host": row.get("ssh_host"),
            "port": row.get("ssh_port"),
            "user": row.get("ssh_user"),
        },
        "workdir": row.get("workdir"),
        # The work folder on the box; the agent rsyncs what it needs off it over
        # SSH before the sandbox is destroyed (nothing is copied automatically).
        "experiment_dir": remote_dir,
        # VM-local conventional home for datasets/caches. Never copied —
        # like everything else outside the work folder.
        "data_dir": data_dir,
        "volume": row.get("volume_name"),
        "gpu": row.get("gpu") or None,
        "cpu": row.get("cpu"),
        "memory": row.get("memory"),
        # Empty on pre-multi-provider rows = the configured default backend.
        "provider": row.get("provider") or None,
        "instance_type": row.get("instance_type") or None,
        "region": row.get("region") or None,
        "public_key_source": row.get("public_key_source") or "managed",
        "expires_at": row.get("expires_at"),
        "storage_enabled": bool(storage_enabled),
    }
    facts["hint"] = _runtime_hint(
        status=str(status),
        ssh=facts["ssh"],
        remote_dir=remote_dir,
        expires_at=row.get("expires_at"),
        storage_enabled=bool(storage_enabled),
    )
    if env_info.get("available_tokens"):
        facts["environment"] = env_info
    if status == "provisioning":
        facts["phase"] = row.get("phase") or "starting"
        facts["detail"] = row.get("detail") or ""
        facts["poll_after_seconds"] = POLL_AFTER_SECONDS
    elif status == "failed":
        facts["error"] = row.get("error") or "provisioning failed"
    if reused is not None:
        facts["reused"] = reused
    return facts

def agent_summary(*, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sandbox_uid": row.get("sandbox_uid"),
        "experiment_id": row.get("experiment_id"),
        "active_experiment_ids": list(row.get("active_experiment_ids") or []),
        "sandbox_id": row.get("sandbox_id"),
        "status": row.get("status"),
        "gpu": row.get("gpu") or None,
        "provider": row.get("provider") or None,
        "instance_type": row.get("instance_type") or None,
        "region": row.get("region") or None,
        "expires_at": row.get("expires_at"),
    }


def sandbox_row_view(
    *,
    row: dict[str, Any],
) -> dict[str, Any]:
    """Canonical sandbox-row projection (workflow status + HTTP/UI)."""
    active_experiment_ids = list(row.get("active_experiment_ids") or [])
    remote_dir, data_dir = _sandbox_dirs(row=row)
    view = {
        "sandbox_uid": row.get("sandbox_uid"),
        "experiment_id": row.get("experiment_id"),
        "active_experiment_ids": active_experiment_ids,
        "project_id": row.get("project_id"),
        "sandbox_id": row.get("sandbox_id"),
        "status": row.get("status"),
        "phase": row.get("phase") or "",
        "detail": row.get("detail") or "",
        "error": row.get("error") or "",
        "gpu": row.get("gpu") or "",
        "cpu": row.get("cpu"),
        "memory": row.get("memory"),
        "provider": row.get("provider") or "",
        "instance_type": row.get("instance_type") or "",
        "region": row.get("region") or "",
        "public_key_source": row.get("public_key_source") or "managed",
        "time_limit": row.get("time_limit"),
        "ssh_host": row.get("ssh_host"),
        "ssh_port": row.get("ssh_port"),
        "ssh_user": row.get("ssh_user"),
        "workdir": row.get("workdir"),
        # Stable API name: `sync_dir` is the remote experiment directory.
        "sync_dir": remote_dir,
        "sandbox_data_dir": data_dir,
        "volume_name": row.get("volume_name"),
        "requested_at": row.get("requested_at"),
        "expires_at": row.get("expires_at"),
        "last_seen_at": row.get("last_seen_at"),
        "terminated_at": row.get("terminated_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    return view


def needs_selection_view(
    *,
    experiment_id: str,
    project_id: str,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """The 'pick a machine' response for bundled-hardware backends."""
    options = catalog.get("options", [])
    cheapest = options[0]["instance_type"] if options else None
    providers = catalog.get("providers")
    view = {
        "experiment_id": experiment_id,
        "project_id": project_id,
        "status": "needs_selection",
        "provider": catalog.get("provider"),
        "select_with": catalog.get("select_with") or "instance_type",
        "reason": catalog.get("reason")
        or "This provider bundles GPU + CPU + RAM into fixed machine types.",
        "options": options,
        "regions": catalog.get("regions", []),
        "hint": (
            "No sandbox is attached and this provider procures whole machines, "
            "so choose one before provisioning. Re-call sandbox.request with "
            "instance_type=<one of options[].instance_type> (and optionally "
            "region=<one of that option's regions>"
            + (
                "; multiple compute providers are configured, so also pass the "
                "chosen option's provider"
                if providers
                else ""
            )
            + "). Options are sorted cheapest-first"
            + (f"; cheapest available now is '{cheapest}'. " if cheapest else ". ")
            + "Call sandbox.options anytime to re-list current availability."
        ),
    }
    if providers:
        view["providers"] = providers
    return view

"""Compute-provider connection routes (Sandboxes → Configure).

The overview is readable by any project principal — it carries which fields
are set and non-secret values, never a secret. Writes (credentials, the
enable switch) are a human's call: a Supabase browser session or the trusted
local principal, mirroring the personal-token rule in ``user_settings`` —
an ``mk_``/``rr_sk_`` machine key must not rewire a project's clouds.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

from ....kernel.utils import ValidationError
from ...identity import (
    HumanSessionRequiredError,
    is_human_session,
    is_local_principal,
)
from ...sandbox_providers import SandboxProviderSettings
from .shared import JsonBody


def build_router(*, providers: SandboxProviderSettings) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/sandbox-providers")
    def provider_overview(project_id: str) -> dict[str, object]:
        return providers.overview(project_id=project_id)

    @router.put("/api/projects/{project_id}/sandbox-providers/{provider}")
    def save_credentials(
        project_id: str,
        provider: str,
        request: Request,
        body: JsonBody = Body(default=None),
    ) -> dict[str, object]:
        _require_human(request)
        payload = body or {}
        values = payload.get("values")
        mode = payload.get("mode")
        if values is None and mode is None:  # accept a flat {field: value} body
            values = {
                key: value
                for key, value in payload.items()
                if key not in ("values", "mode")
            }
        return providers.set_credentials(
            project_id=project_id,
            provider=provider,
            values=values,
            mode=None if mode is None else str(mode),
        )

    @router.post("/api/projects/{project_id}/sandbox-providers/{provider}/enabled")
    def set_enabled(
        project_id: str,
        provider: str,
        request: Request,
        body: JsonBody = Body(default=None),
    ) -> dict[str, object]:
        _require_human(request)
        payload = body or {}
        enabled = payload.get("enabled")
        # bool("false") is True — accept only a real JSON boolean.
        if not isinstance(enabled, bool):
            raise ValidationError("enabled must be a boolean")
        return providers.set_enabled(
            project_id=project_id,
            provider=provider,
            enabled=enabled,
        )

    @router.post(
        "/api/projects/{project_id}/sandbox-providers/{provider}/daily-limit"
    )
    def set_daily_limit(
        project_id: str,
        provider: str,
        request: Request,
        body: JsonBody = Body(default=None),
    ) -> dict[str, object]:
        _require_human(request)
        payload = body or {}
        return providers.set_daily_limit(
            project_id=project_id,
            provider=provider,
            daily_usd_limit=payload.get("daily_usd_limit"),
        )

    @router.post("/api/projects/{project_id}/sandbox-providers/{provider}/verify")
    def verify(
        project_id: str, provider: str, request: Request
    ) -> dict[str, object]:
        # Verification spends the caller's saved/platform credentials on one
        # real provider call — a human's action, like saving them was.
        _require_human(request)
        return providers.verify(project_id=project_id, provider=provider)

    return router


def _require_human(request: Request) -> None:
    principal = request.state.principal
    if is_human_session(principal) or is_local_principal(principal):
        return
    raise HumanSessionRequiredError(
        "configuring compute providers requires a browser session"
    )


__all__ = ["build_router"]

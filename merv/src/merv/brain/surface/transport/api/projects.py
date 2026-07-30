"""Projects HTTP routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import Response

from ....application import Application
from ....research_core import Research
from ....sandbox import SandboxEngine
from .shared import (
    JsonBody,
    conditional_json_from_signal,
    path_scoped_body,
    require_membership_author,
)

from .gateway import ToolInvocationGateway
from .views import present


def build_router(
    gateway: ToolInvocationGateway,
    *,
    application: Application,
    research: Research,
    sandboxes: SandboxEngine,
) -> APIRouter:
    api_router = APIRouter()

    @api_router.get("/api/projects")
    def list_projects(request: Request) -> dict[str, Any]:
        return gateway.call_http(request, name="project.list", arguments={})

    @api_router.post("/api/projects", status_code=201)
    def create_project(
        request: Request, body: JsonBody = Body(default=None)
    ) -> dict[str, Any]:
        payload = body or {}
        return gateway.call_http(
            request,
            name="project",
            arguments={
                "action": "create",
                "name": payload.get("name")
                or payload.get("title")
                or "Untitled Project",
                "summary": payload.get("summary")
                or payload.get("description")
                or payload.get("research_goal")
                or "",
            },
        )

    @api_router.get("/api/projects/{project_id}/members")
    def list_members(project_id: str) -> dict[str, Any]:
        return research.project_members(project_id=project_id)

    @api_router.post("/api/projects/{project_id}/members", status_code=201)
    def add_member(
        project_id: str, request: Request, body: JsonBody = Body(default=None)
    ) -> dict[str, Any]:
        # Any human MEMBER may share the project (the membership gate already ran).
        require_membership_author(request)
        return research.add_project_member(
            project_id=project_id, user_id=str((body or {}).get("user_id") or "")
        )

    @api_router.delete("/api/projects/{project_id}/members/{user_id}")
    def remove_member(
        project_id: str, user_id: str, request: Request
    ) -> dict[str, Any]:
        require_membership_author(request)
        return research.remove_project_member(project_id=project_id, user_id=user_id)

    @api_router.get("/api/projects/{project_id}")
    def get_project(project_id: str, request: Request) -> dict[str, Any]:
        return gateway.call_http(
            request, name="project.get", arguments={"project_id": project_id}
        )

    @api_router.patch("/api/projects/{project_id}")
    @api_router.put("/api/projects/{project_id}")
    def update_project(
        project_id: str, request: Request, body: JsonBody = Body(default=None)
    ) -> dict[str, Any]:
        return gateway.call_http(
            request,
            name="project.update",
            arguments=path_scoped_body(body, project_id=project_id),
        )

    @api_router.get("/api/projects/{project_id}/home")
    def home(project_id: str, request: Request) -> Response:
        # Composite signal ETag. The home payload is a pure function of three
        # inputs: the event ledger (claims/experiments/reviews/reflections/
        # artifacts all append events), live sandbox rows (heartbeats bump
        # updated_at but write no event), and the MLflow reachability probe
        # (external, 5s-cached). A 304 skips the heavy status/experiment render.
        return conditional_json_from_signal(
            request,
            signal_parts=(
                "home",
                project_id,
                application.timeline_signal(project_id=project_id),
                sandboxes.project_signal(project_id=project_id),
                json.dumps(
                    application.tracking_health(),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            ),
            payload=lambda: present(application.dashboard(project_id=project_id)),
        )

    @api_router.get("/api/projects/{project_id}/status")
    def project_status(
        project_id: str, experiment_id: str | None = None
    ) -> dict[str, Any]:
        # Full shape for the UI (see home()); the tool stays slim for the agent.
        return present(
            application.status(project_id=project_id, experiment_id=experiment_id)
        )

    return api_router

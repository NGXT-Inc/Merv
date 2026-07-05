"""Projects HTTP routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import Response, StreamingResponse

from ... import __version__
from ...services.identity import LOCAL_PRINCIPAL
from ...utils import NotFoundError, ValidationError
from ...version import meta
from .shared import JsonBody, conditional_json

from .context import ApiRouteContext


def build_router(ctx: ApiRouteContext) -> APIRouter:
    api_router = APIRouter()
    api = ctx.api
    router = ctx.project_router
    surface = ctx.surface
    api_for_project = ctx.api_for_project
    default_api = ctx.default_api
    route_call_tool = ctx.route_call_tool
    require_data_plane_for_http = ctx.require_data_plane_for_http
    @api_router.get("/api/projects")
    def list_projects(request: Request) -> dict[str, Any]:
        if router is not None:
            return router.list_projects()
        assert api is not None
        return api.call_tool(name="project.list", arguments={})

    @api_router.post("/api/projects", status_code=201)
    def create_project(request: Request, body: JsonBody = Body(default=None)) -> dict[str, Any]:
        payload = body or {}
        if router is not None:
            repo_root = payload.get("repo_root") or payload.get("directory") or payload.get("path")
            if not repo_root:
                raise ValidationError("repo_root is required", details={"field": "repo_root"})
            name = payload.get("name") or payload.get("title") or "Untitled Project"
            summary = payload.get("summary") or payload.get("description") or payload.get("research_goal") or ""
            return router.create_project(
                repo_root=repo_root,
                name=name,
                summary=summary,
            )
        assert api is not None
        return api.create_project(body=payload, tenant_id=None)

    @api_router.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        return api_for_project(project_id).call_tool(name="project.get", arguments={"project_id": project_id})

    @api_router.patch("/api/projects/{project_id}")
    @api_router.put("/api/projects/{project_id}")
    def update_project(project_id: str, body: JsonBody = Body(default=None)) -> dict[str, Any]:
        return api_for_project(project_id).call_tool(name="project.update", arguments={"project_id": project_id, **(body or {})})

    @api_router.get("/api/projects/{project_id}/home")
    def home(project_id: str, request: Request) -> Response:
        return conditional_json(request, api_for_project(project_id).home(project_id=project_id))

    @api_router.get("/api/projects/{project_id}/status")
    def project_status(project_id: str, experiment_id: str | None = None) -> dict[str, Any]:
        # Full shape for the UI (see home()); the tool stays slim for the agent.
        target = api_for_project(project_id)
        return target._present(
            target.app.workflow.status_and_next(
                project_id=project_id, experiment_id=experiment_id
            )
        )


    return api_router

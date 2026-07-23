"""Projects HTTP routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import Response

from ....application.facade import EventTimelineQuery, ProjectDashboardQuery, StatusAndNextQuery
from ....research_core.facade import ResearchProjects
from ....sandbox.facade import SandboxFacade
from .shared import JsonBody, conditional_json_from_signal, path_scoped_body

from .context import ApiRouteContext
from .views import present


def build_router(
    ctx: ApiRouteContext,
    *,
    projects: ResearchProjects,
    dashboard: ProjectDashboardQuery,
    workflow: StatusAndNextQuery,
    timeline: EventTimelineQuery,
    sandboxes: SandboxFacade,
) -> APIRouter:
    api_router = APIRouter()

    @api_router.get("/api/projects")
    def list_projects(request: Request) -> dict[str, Any]:
        return ctx.call_tool(request, name="project.list", arguments={})

    @api_router.post("/api/projects", status_code=201)
    def create_project(
        request: Request, body: JsonBody = Body(default=None)
    ) -> dict[str, Any]:
        payload = body or {}
        return ctx.call_tool(
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
        return projects.members(project_id=project_id)

    @api_router.post("/api/projects/{project_id}/members", status_code=201)
    def add_member(
        project_id: str, body: JsonBody = Body(default=None)
    ) -> dict[str, Any]:
        # Any member may share the project (the membership gate already ran).
        return projects.add_member(
            project_id=project_id, user_id=str((body or {}).get("user_id") or "")
        )

    @api_router.delete("/api/projects/{project_id}/members/{user_id}")
    def remove_member(project_id: str, user_id: str) -> dict[str, Any]:
        return projects.remove_member(project_id=project_id, user_id=user_id)

    @api_router.get("/api/projects/{project_id}")
    def get_project(project_id: str, request: Request) -> dict[str, Any]:
        return ctx.call_tool(
            request, name="project.get", arguments={"project_id": project_id}
        )

    @api_router.patch("/api/projects/{project_id}")
    @api_router.put("/api/projects/{project_id}")
    def update_project(
        project_id: str, request: Request, body: JsonBody = Body(default=None)
    ) -> dict[str, Any]:
        return ctx.call_tool(
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
                timeline.signal(project_id=project_id),
                sandboxes.project_signal(project_id=project_id),
                json.dumps(
                    dashboard.health(),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            ),
            payload=lambda: present(dashboard(project_id=project_id)),
        )

    @api_router.get("/api/projects/{project_id}/status")
    def project_status(
        project_id: str, experiment_id: str | None = None
    ) -> dict[str, Any]:
        # Full shape for the UI (see home()); the tool stays slim for the agent.
        return present(
            workflow.status_and_next(
                project_id=project_id, experiment_id=experiment_id
            )
        )

    return api_router

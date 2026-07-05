"""Sandboxes HTTP routes."""

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
    @api_router.get("/api/projects/{project_id}/sandboxes")
    def list_sandboxes(project_id: str, request: Request) -> Response:
        return conditional_json(
            request, api_for_project(project_id).sandbox_list_view(project_id=project_id)
        )

    @api_router.get("/api/sandboxes/health")
    def sandbox_health() -> dict[str, Any]:
        if router is not None:
            return {"ok": True, "mode": "multi_project"}
        assert api is not None
        return api.sandbox_health_view()

    @api_router.get("/api/projects/{project_id}/experiments/{experiment_id}/sandbox")
    def get_sandbox(
        project_id: str, experiment_id: str, sandbox_uid: str | None = None
    ) -> dict[str, Any]:
        return api_for_project(project_id).sandbox_get_view(
            project_id=project_id, experiment_id=experiment_id, sandbox_uid=sandbox_uid
        )

    @api_router.get("/api/projects/{project_id}/sandboxes/{sandbox_uid}")
    def get_sandbox_by_uid(project_id: str, sandbox_uid: str) -> dict[str, Any]:
        return api_for_project(project_id).sandbox_get_view(
            project_id=project_id, sandbox_uid=sandbox_uid
        )

    @api_router.get("/api/projects/{project_id}/experiments/{experiment_id}/sandbox/metrics")
    def sandbox_metrics(
        project_id: str, experiment_id: str, sandbox_uid: str | None = None
    ) -> dict[str, Any]:
        return api_for_project(project_id).sandbox_metrics_view(
            project_id=project_id, experiment_id=experiment_id, sandbox_uid=sandbox_uid
        )

    @api_router.get("/api/projects/{project_id}/sandboxes/{sandbox_uid}/metrics")
    def sandbox_metrics_by_uid(project_id: str, sandbox_uid: str) -> dict[str, Any]:
        return api_for_project(project_id).sandbox_metrics_view(
            project_id=project_id, sandbox_uid=sandbox_uid
        )

    @api_router.get("/api/projects/{project_id}/experiments/{experiment_id}/sandbox/terminal")
    def sandbox_terminal(
        project_id: str,
        experiment_id: str,
        tail: int | None = None,
        since: int | None = None,
        sandbox_uid: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"project_id": project_id, "experiment_id": experiment_id}
        if sandbox_uid:
            args["sandbox_uid"] = sandbox_uid
        if tail is not None:
            args["tail"] = tail
        if since is not None:
            args["since"] = since
        return api_for_project(project_id).call_tool(name="sandbox.terminal", arguments=args)

    @api_router.get("/api/projects/{project_id}/sandboxes/{sandbox_uid}/terminal")
    def sandbox_terminal_by_uid(
        project_id: str,
        sandbox_uid: str,
        tail: int | None = None,
        since: int | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"project_id": project_id, "sandbox_uid": sandbox_uid}
        if tail is not None:
            args["tail"] = tail
        if since is not None:
            args["since"] = since
        return api_for_project(project_id).call_tool(name="sandbox.terminal", arguments=args)

    @api_router.post("/api/projects/{project_id}/experiments/{experiment_id}/sandbox/release")
    def release_sandbox(
        project_id: str,
        experiment_id: str,
        request: Request,
        sandbox_uid: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "project_id": project_id,
            "experiment_id": experiment_id,
            "confirm_retained": True,
        }
        if sandbox_uid:
            arguments["sandbox_uid"] = sandbox_uid
        return route_call_tool(
            name="sandbox.release",
            # The browser already confirms in its own UX; the retention gate is
            # for the agent's MCP call, so the UI route terminates directly.
            arguments=arguments,
            activity_source="http",
            principal=getattr(request.state, "principal", LOCAL_PRINCIPAL),
        )

    @api_router.post("/api/projects/{project_id}/sandboxes/{sandbox_uid}/release")
    def release_sandbox_by_uid(
        project_id: str,
        sandbox_uid: str,
        request: Request,
    ) -> dict[str, Any]:
        return route_call_tool(
            name="sandbox.release",
            arguments={
                "project_id": project_id,
                "sandbox_uid": sandbox_uid,
                "confirm_retained": True,
            },
            activity_source="http",
            principal=getattr(request.state, "principal", LOCAL_PRINCIPAL),
        )


    return api_router

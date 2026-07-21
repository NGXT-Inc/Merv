"""Resources HTTP routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ....application.facade import HostedResourceContentQuery
from ....artifacts.facade import ArtifactRecords, Artifacts
from .context import ApiRouteContext
from .views import resource_file as select_resource_file
from .views import resources_tree as build_resources_tree


def build_router(
    ctx: ApiRouteContext,
    *,
    records: ArtifactRecords,
    artifacts: Artifacts,
    content_query: HostedResourceContentQuery,
) -> APIRouter:
    api_router = APIRouter()

    @api_router.get("/api/projects/{project_id}/resources")
    def list_resources(project_id: str, kind: str | None = None) -> dict[str, Any]:
        items = records.list_resources(project_id=project_id)["resources"]
        return {"resources": [item for item in items if not kind or item.get("kind") == kind]}

    @api_router.get("/api/projects/{project_id}/resources/tree")
    def resources_tree(project_id: str) -> dict[str, Any]:
        return build_resources_tree(
            records.list_resources(project_id=project_id)["resources"]
        )

    @api_router.get("/api/projects/{project_id}/resources/{resource_id}")
    def resolve_resource(
        project_id: str, resource_id: str, request: Request
    ) -> dict[str, Any]:
        return ctx.call_tool(
            request,
            name="resource.find",
            arguments={"project_id": project_id, "resource_id": resource_id},
        )

    @api_router.get("/api/projects/{project_id}/resources/{resource_id}/history")
    def resource_history(project_id: str, resource_id: str) -> dict[str, Any]:
        resource = records.resolve(
            resource_id=resource_id, project_id=project_id, include_history=True
        )
        versions = resource.pop("versions", [])
        return {"resource": resource, "versions": versions}

    @api_router.delete("/api/projects/{project_id}/resources/{resource_id}")
    def delete_resource(
        project_id: str, resource_id: str, request: Request
    ) -> dict[str, Any]:
        return ctx.call_tool(
            request,
            name="resource.delete",
            arguments={"project_id": project_id, "resource_id": resource_id},
        )

    @api_router.get("/api/projects/{project_id}/resources/{resource_id}/content")
    def resource_content(
        project_id: str, resource_id: str, version: str | None = None
    ) -> dict[str, Any]:
        # `version` pins the exact submitted bytes of one resource version
        # (faithful historical rendering for reflection-wave
        # artifacts).
        # Omitted → unchanged behavior (latest gated bytes / live file).
        return content_query(
            project_id=project_id, resource_id=resource_id, version_id=version
        )

    @api_router.get("/api/projects/{project_id}/resources/{resource_id}/file")
    def resource_file(
        project_id: str, resource_id: str, rel: str | None = None
    ) -> Response:
        content, headers = select_resource_file(
            artifacts,
            project_id=project_id, resource_id=resource_id, rel=rel
        )
        content_type = headers.pop("Content-Type", "application/octet-stream")
        return Response(content=content, media_type=content_type, headers=headers)

    return api_router

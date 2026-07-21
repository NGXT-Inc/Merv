"""Storage HTTP routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ....application.ports.storage import ObjectStorage
from ....kernel.utils import NotFoundError

def build_router(*, storage: ObjectStorage | None) -> APIRouter:
    api_router = APIRouter()
    def storage_for_project(project_id: str) -> ObjectStorage:
        if storage is None:
            raise NotFoundError("storage is not enabled on this backend")
        return storage

    @api_router.get("/api/projects/{project_id}/storage")
    def list_storage(
        project_id: str,
        kind: str | None = None,
        status: str | None = None,
        name: str | None = None,
        include_expired: bool = False,
    ) -> dict[str, Any]:
        return storage_for_project(project_id).list_objects(
            project_id=project_id,
            kind=kind,
            status=status,
            name=name,
            include_expired=include_expired,
        )

    @api_router.get("/api/projects/{project_id}/storage/{object_id}")
    def get_storage_object(project_id: str, object_id: str) -> dict[str, Any]:
        return storage_for_project(project_id).get_object(
            project_id=project_id, object_id=object_id
        )

    @api_router.post("/api/projects/{project_id}/storage/{object_id}/download")
    def download_storage_object(project_id: str, object_id: str) -> dict[str, Any]:
        return storage_for_project(project_id).resolve(
            project_id=project_id, object_id=object_id, include_download=True
        )

    @api_router.post("/api/projects/{project_id}/storage/{object_id}/pin")
    def pin_storage_object(project_id: str, object_id: str) -> dict[str, Any]:
        return {"object": storage_for_project(project_id).pin(
            project_id=project_id, object_id=object_id
        )}

    @api_router.post("/api/projects/{project_id}/storage/{object_id}/unpin")
    def unpin_storage_object(project_id: str, object_id: str) -> dict[str, Any]:
        return {"object": storage_for_project(project_id).unpin(
            project_id=project_id, object_id=object_id
        )}

    @api_router.post("/api/projects/{project_id}/storage/{object_id}/renew")
    def renew_storage_object(project_id: str, object_id: str) -> dict[str, Any]:
        return {"object": storage_for_project(project_id).renew(
            project_id=project_id, object_id=object_id
        )}

    @api_router.delete("/api/projects/{project_id}/storage/{object_id}")
    def delete_storage_object(project_id: str, object_id: str) -> dict[str, Any]:
        return storage_for_project(project_id).delete(
            project_id=project_id, object_id=object_id
        )


    return api_router

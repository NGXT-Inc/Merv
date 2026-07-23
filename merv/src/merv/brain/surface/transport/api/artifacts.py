"""Artifact HTTP routes: token-bearer uploads plus UI reads.

The PUT routes are auth-exempt (see RequestAuthenticator): the one-time upload
token minted by artifact.submit is the credential, so the agent's bare
``curl -T`` works against both local and hosted brains.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ....artifacts.facade import ArtifactSubmissions, upload_command
from ....kernel.utils import ValidationError


def _too_large(cap: int) -> JSONResponse:
    return JSONResponse(
        {
            "detail": (
                f"upload exceeds the maximum of {cap} bytes for this token — slim "
                "the file (move raw data/outputs elsewhere and reference them) "
                "and re-run the upload command"
            ),
            "error_code": "payload_too_large",
            "max_bytes": cap,
        },
        status_code=413,
    )


async def _read_capped(request: Request, *, cap: int) -> bytes | None:
    """Body bytes, or None once the cap is exceeded (never buffers past it)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        return None
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > cap:
            return None
    return bytes(data)


def build_router(*, submissions: ArtifactSubmissions) -> APIRouter:
    api_router = APIRouter()

    @api_router.put("/api/artifacts/u/{token}")
    async def upload_artifact(token: str, request: Request) -> Any:
        # Token first: an unknown token 404s before any body byte is buffered.
        cap = submissions.pending_upload_cap(token=token)
        data = await _read_capped(request, cap=cap)
        if data is None:
            return _too_large(cap)
        try:
            result = submissions.complete_upload(token=token, data=data)
        except ValidationError as exc:
            if "max_bytes" in exc.details:
                return JSONResponse(
                    {"detail": exc.message, "error_code": "payload_too_large", **exc.details},
                    status_code=413,
                )
            raise
        base = str(request.base_url).rstrip("/")
        # Follow-up one-liners so the agent pushes each referenced figure the
        # same way it pushed the document. Links are relative to the document,
        # so the upload source joins them onto its path label's directory.
        doc_dir = result["path"].rsplit("/", 1)[0] if "/" in result["path"] else ""
        result["figures"] = [
            {
                "link_path": figure["link_path"],
                "run": upload_command(
                    base_url=base,
                    path=f"{doc_dir}/{figure['link_path']}" if doc_dir else figure["link_path"],
                    token=figure["token"],
                    kind="f",
                ),
            }
            for figure in result["figures"]
        ]
        return result

    @api_router.put("/api/artifacts/f/{token}")
    async def upload_figure(token: str, request: Request) -> Any:
        cap = submissions.pending_upload_cap(token=token, kind="f")
        data = await _read_capped(request, cap=cap)
        if data is None:
            return _too_large(cap)
        try:
            return submissions.complete_figure_upload(token=token, data=data)
        except ValidationError as exc:
            if "max_bytes" in exc.details:
                return JSONResponse(
                    {"detail": exc.message, "error_code": "payload_too_large", **exc.details},
                    status_code=413,
                )
            raise

    @api_router.get("/api/projects/{project_id}/artifacts")
    def list_artifacts(
        project_id: str,
        target_type: str = "",
        target_id: str = "",
        role: str = "",
    ) -> dict[str, Any]:
        return submissions.find(
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            role=role,
        )

    @api_router.get("/api/projects/{project_id}/artifacts/{artifact_id}/content")
    def artifact_content(project_id: str, artifact_id: str) -> dict[str, Any]:
        return submissions.artifact_content(
            project_id=project_id, artifact_id=artifact_id
        )

    @api_router.get("/api/projects/{project_id}/artifacts/{artifact_id}/file")
    def artifact_file(project_id: str, artifact_id: str) -> Response:
        data, content_type, filename = submissions.artifact_file(
            project_id=project_id, artifact_id=artifact_id
        )
        return Response(
            content=data,
            media_type=content_type,
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    @api_router.get("/api/projects/{project_id}/artifacts/{artifact_id}/figure")
    def artifact_figure(project_id: str, artifact_id: str, rel: str) -> Response:
        data = submissions.figure_bytes(
            project_id=project_id, artifact_id=artifact_id, link_path=rel
        )
        if data is None:
            return JSONResponse(
                {"detail": f"figure not found: {rel}", "error_code": "not_found"},
                status_code=404,
            )
        return Response(content=data, media_type="application/octet-stream")

    return api_router

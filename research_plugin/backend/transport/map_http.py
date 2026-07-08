"""Self-contained HTTP routes for the Research Map (RESEARCH_MAP_V1.md).

Mirrors ``feed_http.py``: the map owns its routes so it stays liftable — the
single integration point is one ``register_map_routes`` call in the app
assembly. Everything reads off ``app.research_map``.

``/map/snapshot`` serves the SAME server-rendered pixels the agent snapshot
tools return (one renderer, two consumers — the parity hard line).
``/map/state`` exists for UI hit-testing/drag only and is never an agent
surface.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import Body, Query, Request
from fastapi.responses import Response

from ..utils import ValidationError

# Server-rendered PNGs served same-origin; stop MIME sniffing (feed_http.py).
_IMAGE_HEADERS = {"X-Content-Type-Options": "nosniff"}


def register_map_routes(http: Any, *, app_for: Callable[[str, Request], Any]) -> None:
    """Register the map's `/api/projects/{pid}/map*` routes onto ``http``."""

    @http.get("/api/projects/{project_id}/map/snapshot")
    def map_snapshot(
        request: Request,
        project_id: str,
        cx: float | None = Query(None),
        cy: float | None = Query(None),
        zoom: float | None = Query(None),
        cell: str | None = Query(None),
        w: int = Query(1200, ge=160, le=2400),
        h: int = Query(800, ge=160, le=2400),
        scale: float = Query(1.0, ge=1.0, le=3.0),
    ) -> Response:
        png, meta = app_for(project_id, request).research_map.snapshot(
            project_id=project_id, cx=cx, cy=cy, zoom=zoom, cell=cell, w=w, h=h, scale=scale
        )
        return Response(
            content=png,
            media_type="image/png",
            headers={**_IMAGE_HEADERS, "X-RP-Map-Layout-Version": str(meta["layout_version"])},
        )

    @http.get("/api/projects/{project_id}/map/state")
    def map_state(request: Request, project_id: str) -> dict[str, Any]:
        return app_for(project_id, request).research_map.state(project_id=project_id)

    @http.post("/api/projects/{project_id}/map/pin")
    def map_pin(
        request: Request, project_id: str, body: Any = Body(default=None)
    ) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise ValidationError("pin body must be a JSON object")
        try:
            x, y = float(body.get("x")), float(body.get("y"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("pin requires numeric x and y") from exc
        return app_for(project_id, request).research_map.pin(
            project_id=project_id,
            entity_id=str(body.get("entity_id") or ""),
            x=x,
            y=y,
        )

    @http.post("/api/projects/{project_id}/map/unpin")
    def map_unpin(
        request: Request, project_id: str, body: Any = Body(default=None)
    ) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise ValidationError("unpin body must be a JSON object")
        return app_for(project_id, request).research_map.unpin(
            project_id=project_id,
            entity_id=str(body.get("entity_id") or ""),
        )

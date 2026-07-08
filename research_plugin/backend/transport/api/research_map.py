"""Research Map route composition."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..map_http import register_map_routes
from .context import ApiRouteContext


def build_router(ctx: ApiRouteContext) -> APIRouter:
    api_router = APIRouter()

    def app_for_map(project_id: str, request: Request):
        return ctx.api_for_project(project_id).app

    register_map_routes(api_router, app_for=app_for_map)
    return api_router

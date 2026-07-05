"""Context shared by resource route modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ...app import ResearchPluginApp
from ..http_policy import HttpSurfacePolicy
from ...daemon.project_router import ProjectRouter
from .views import ResearchHttpApi


@dataclass(frozen=True)
class ApiRouteContext:
    api: ResearchHttpApi | None
    project_router: ProjectRouter | None
    surface: HttpSurfacePolicy
    cleanup: Any | None
    api_for_project: Callable[[str], ResearchHttpApi]
    default_api: Callable[[], ResearchHttpApi | None]
    route_call_tool: Callable[..., dict[str, Any]]
    app_for_data_plane_project: Callable[..., ResearchPluginApp]

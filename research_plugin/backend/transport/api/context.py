"""Context shared by resource route modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..http_policy import HttpSurfacePolicy
from .views import ResearchHttpApi


@dataclass(frozen=True)
class ApiRouteContext:
    api: ResearchHttpApi
    surface: HttpSurfacePolicy
    cleanup: Any | None
    api_for_project: Callable[[str], ResearchHttpApi]
    route_call_tool: Callable[..., dict[str, Any]]
    app_for_data_plane_project: Callable[..., Any]

"""Lean FastAPI composition root for the Merv HTTP surface."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI

from .... import __version__
from ....kernel.secret_tokens import MIN_WAIT_SECRET_BYTES
from ...auth import require_hosted_auth_decision
from ..admin_http import register_admin_routes
from ..feed_http import register_feed_routes
from ..http_policy import HttpSurfacePolicy
from ..mcp_http import register_mcp_routes
from . import artifacts, claims, events, experiments, litreview, mcp_preauth, meta, oauth, projects, reflections, reviews, runs_wait, sandboxes, storage, user_settings
from .context import ApiRouteContext
from .dependencies import HttpDependencies
from .gateway import (
    ProjectAuthorizer,
    RequestAuthenticator,
    ToolInvocationGateway,
    install_auth_routes,
    install_request_middleware,
)
from .middleware import (
    install_activity_middleware,
    install_cors,
    install_error_handlers,
)
def create_fastapi_app(
    app: HttpDependencies | None = None,
    *,
    allowed_origins: list[str] | None = None,
    cleanup: Any | None = None,
    tenant_counters: Any | None = None,
    surface_policy: HttpSurfacePolicy | None = None,
    auth: Any | None = None,
    oauth_service: Any | None = None,
    ui_base_url: str = "",
    oauth_resource_uri: str = "",
    wait_secret: bytes | None = None,
    env: Mapping[str, str] | None = None,
) -> FastAPI:
    """Compose transport adapters around an already-built backend."""
    if app is None:
        raise ValueError("provide app")
    if oauth_service is not None and not oauth_resource_uri:
        raise ValueError("oauth_resource_uri is required when OAuth is enabled")
    surface = surface_policy or HttpSurfacePolicy.for_surface(
        restrict_cors=False, hosted_control=False
    )
    require_hosted_auth_decision(auth=auth, hosted=surface.hosted_control, env=env)
    api = app
    # Validated before any wiring: a bad key must refuse this composition
    # without touching state a sibling app over the same backend relies on.
    if wait_secret and len(wait_secret) < MIN_WAIT_SECRET_BYTES:
        raise ValueError(
            f"wait secret must be at least {MIN_WAIT_SECRET_BYTES} bytes"
        )
    authorizer = ProjectAuthorizer(projects=api.projects)
    # One key, both directions: the gateway signs sandbox.runs wait URLs with
    # exactly what the route below verifies, per composition — never shared.
    gateway = ToolInvocationGateway(
        tools=api.tools, reviews=api.reviews, sandboxes=api.sandboxes,
        surface=surface, projects=authorizer, ledger=api.tool_ledger,
        wait_secret=wait_secret)
    authenticator = RequestAuthenticator(
        surface=surface, verifier=auth, oauth_enabled=oauth_service is not None,
        canonical_mcp_resource=oauth_resource_uri)
    http = FastAPI(title="Merv API", version=__version__)

    install_request_middleware(http, authenticator=authenticator, authorizer=authorizer,
                               ledger=api.tool_ledger)
    install_activity_middleware(http, structured_logger=api.structured_log)
    # Registered last so CORS decorates middleware short-circuits as well.
    install_cors(http, allowed_origins=allowed_origins, surface=surface)
    install_error_handlers(http)
    install_auth_routes(http, verifier=auth, owner_key_audience=oauth_resource_uri)
    oauth.install_routes(http, service=oauth_service, allowed_origins=allowed_origins or [],
                         ui_base_url=ui_base_url, canonical_mcp_resource=oauth_resource_uri)

    ctx = ApiRouteContext(surface=surface, route_call_tool=gateway.call,
                          auth_meta=auth.meta() if auth is not None else None)
    routers = (
        meta.build_router(ctx, activity_log=api.activity, tool_calls=api.tool_calls, projects=api.projects),
        projects.build_router(
            ctx, projects=api.projects, dashboard=api.dashboard,
            workflow=api.workflow, timeline=api.timeline, sandboxes=api.sandboxes),
        claims.build_router(ctx),
        experiments.build_router(
            ctx,
            collection=api.experiment_collection,
            detail=api.experiment_detail,
            workflow=api.workflow,
            figure=api.experiment_figure,
            graphs=api.logic_graph,
            tracking=api.tracking_overview,
        ),
        reflections.build_router(graphs=api.logic_graph),
        litreview.build_router(literature=api.literature),
        artifacts.build_router(submissions=api.artifact_submissions),
        storage.build_router(storage=api.storage),
        reviews.build_router(ctx, review_delivery=api.reviews),
        sandboxes.build_router(ctx, sandboxes=api.sandboxes, cost_query=api.compute_cost),
        events.build_router(timeline=api.timeline),
        runs_wait.build_router(sandboxes=api.sandboxes, secret=wait_secret),
        user_settings.build_router(user_settings=api.user_settings),
    )
    for router in routers:
        http.include_router(router)
    register_feed_routes(
        http,
        feed_api=api.feed,
        authorize_project=gateway.authorize_project,
        activity=api.activity,
    )
    register_mcp_routes(
        http, list_tools=api.tools.list_tools, call_tool=gateway.call_mcp,
        allow_tool=lambda _tool: True,
        authorize_scope=mcp_preauth.build_mcp_preauthorizer(
            authorizer=authorizer, reviews=api.reviews,
            hosted=surface.use_hosted_tool_policies),
        ledger=api.tool_ledger,
    )
    register_admin_routes(
        http,
        cleanup=cleanup,
        tenant_counters=tenant_counters or api.tenant_counters,
    )
    return http

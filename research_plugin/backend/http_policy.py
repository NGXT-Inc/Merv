"""HTTP surface policy independent of FastAPI route wiring."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostedToolPolicy:
    tenant_id_fallback: str | None = ""
    telemetry_from_review_request: bool = False


@dataclass(frozen=True)
class HttpSurfacePolicy:
    require_bearer_auth: bool
    restrict_cors: bool
    hosted_control: bool
    expose_local_data_plane: bool
    accept_repo_root_context: bool
    allow_data_plane_http: bool
    allow_data_plane_tool_calls: bool
    use_hosted_tool_policies: bool
    enforce_project_scope: bool
    release_uses_final_pull: bool

    @classmethod
    def for_auth_present(cls, auth_present: bool) -> "HttpSurfacePolicy":
        return cls(
            require_bearer_auth=auth_present,
            restrict_cors=auth_present,
            hosted_control=auth_present,
            expose_local_data_plane=not auth_present,
            accept_repo_root_context=not auth_present,
            allow_data_plane_http=not auth_present,
            allow_data_plane_tool_calls=not auth_present,
            use_hosted_tool_policies=auth_present,
            enforce_project_scope=auth_present,
            release_uses_final_pull=not auth_present,
        )


HOSTED_CONTROL_TOOL_POLICIES = {
    "project.create": HostedToolPolicy(tenant_id_fallback=None),
    "project.list": HostedToolPolicy(),
    "project.current": HostedToolPolicy(),
    "review.start": HostedToolPolicy(telemetry_from_review_request=True),
}

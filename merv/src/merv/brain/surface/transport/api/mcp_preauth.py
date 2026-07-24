"""Synchronous pre-flight denials for streamable /mcp tool calls (FIX 6).

Mirrors every gateway dispatch check that can deny a call WITHOUT running the
tool — project scope (key equality + membership), the key project-create
block, and review-request/session-derived scope (INV-9) — so denials always
commit as transport 403s before the SSE stream can open with a 200. The
gateway dispatch path keeps the same checks and stays authoritative.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from fastapi import Request

from ...identity import LOCAL_PRINCIPAL, ProjectKeyScopeError
from ..http_policy import HOSTED_CONTROL_TOOL_POLICIES

Preauthorizer = Callable[[Request, str, dict[str, Any]], None]


class ProjectScopeAuthorizer(Protocol):
    def require_member(self, *, project_id: str | None, principal: Any) -> None: ...

    def key_project_id(self, principal: Any) -> str: ...


class ReviewScopeResolver(Protocol):
    def request_project_id(self, *, review_request_id: Any) -> str: ...

    def session_project_id(self, *, review_session_id: Any) -> str: ...


def build_mcp_preauthorizer(
    *, authorizer: ProjectScopeAuthorizer, reviews: ReviewScopeResolver, hosted: bool
) -> Preauthorizer:
    """Bind the project authorizer + review resolver into a ScopeAuthorizer."""

    def preauthorize(request: Request, name: str, arguments: dict[str, Any]) -> None:
        principal = getattr(request.state, "principal", LOCAL_PRINCIPAL)
        authorizer.require_member(
            project_id=str(arguments.get("project_id") or "") or None,
            principal=principal,
        )
        key_project_id = authorizer.key_project_id(principal)
        if key_project_id and name == "project" and arguments.get("action") == "create":
            raise ProjectKeyScopeError("project API keys cannot create projects",
                                       details={"key_project_id": key_project_id})
        policy = HOSTED_CONTROL_TOOL_POLICIES.get(name) if hosted else None
        if policy is None:
            return
        if policy.telemetry_from_review_request:
            authorizer.require_member(
                project_id=reviews.request_project_id(
                    review_request_id=arguments.get("review_request_id")),
                principal=principal)
        if policy.telemetry_from_review_session:
            # INV-9: the session's own project decides scope, so an mk_ key
            # cannot ride a foreign session id into another project.
            authorizer.require_member(
                project_id=reviews.session_project_id(
                    review_session_id=arguments.get("review_session_id")),
                principal=principal)

    return preauthorize

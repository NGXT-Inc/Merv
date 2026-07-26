"""Shared HTTP API response helpers and CORS policy constants."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import urllib.parse
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from ....kernel.env import env_value
from ....kernel.request_context import bind_principal
from ....kernel.utils import ValidationError
from ...identity import is_local_principal, principal_label

ADMIN_TOKEN_ENV_VAR = "MERV_ADMIN_TOKEN"
ADMIN_TOKEN_HEADER = "X-Admin-Token"

JsonBody = dict[str, Any] | None
UI_CORS_HEADERS = [
    "Content-Type",
    "Accept",
    "Authorization",
    "X-RP-Client-Version",
    "If-None-Match",
]
# ETag is not CORS-safelisted; expose it so a cross-origin dev UI can echo it back.
UI_CORS_EXPOSE_HEADERS = ["ETag"]

# Upload tokens are bearer credentials living in the URL path; the activity log
# must never persist them. Shared choke-point across every auth-exempt token
# route (INV-12): artifact document/figure PUTs, feed-media PUTs, and the
# storage completion POST.
_UPLOAD_TOKEN_PATH_RE = re.compile(r"(/api/(?:artifacts/[uf]|feed/u|storage/u)/)[^/?]+")


def redact_upload_tokens(path: str) -> str:
    return _UPLOAD_TOKEN_PATH_RE.sub(r"\1<redacted>", path)


def path_scoped_body(body: JsonBody, **scope: str) -> dict[str, Any]:
    """Bind route identifiers after parsing a body, rejecting contradictions."""
    payload = dict(body or {})
    conflicts = [
        field
        for field, value in scope.items()
        if field in payload and payload[field] != value
    ]
    if conflicts:
        raise ValidationError(
            "request body scope does not match route",
            details={"fields": conflicts},
        )
    payload.update(scope)
    return payload


def _json_body(payload: Any) -> bytes:
    """Serialize exactly like FastAPI's default JSON path for these handlers."""
    body = json.dumps(
        jsonable_encoder(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return body


def _matches(request: Request, *, etag: str) -> bool:
    if_none_match = request.headers.get("if-none-match") or ""
    return etag in [tag.strip() for tag in if_none_match.split(",")]


def _signal_etag(*parts: object) -> str:
    raw = "\0".join(str(part) for part in parts).encode("utf-8")
    return f'"{hashlib.sha256(raw).hexdigest()[:32]}"'


def conditional_json(request: Request, payload: Any) -> Response:
    """ETag/304 wrapper for body-hash endpoints.

    Serializes exactly like FastAPI's default path (jsonable_encoder +
    compact json.dumps) so 200 bodies stay byte-identical for clients that
    never send If-None-Match; the ETag is a content hash of those bytes.
    """
    body = _json_body(payload)
    etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if _matches(request, etag=etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def conditional_json_from_signal(
    request: Request,
    *,
    signal_parts: tuple[object, ...],
    payload: Callable[[], Any],
) -> Response:
    """ETag/304 wrapper for endpoints with a proven monotonic change signal."""
    etag = _signal_etag(*signal_parts)
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if _matches(request, etag=etag):
        return Response(status_code=304, headers=headers)
    return Response(
        content=_json_body(payload()), media_type="application/json", headers=headers
    )


def is_local_origin(origin: str) -> bool:
    host = (urllib.parse.urlsplit(origin).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


_PROJECT_PATH_RE = re.compile(r"^/api/projects/([^/]+)")


class RefusalLedger(Protocol):
    """Durable sink for calls refused before the dispatcher ever sees them."""

    def reject(self, **kwargs: Any) -> None: ...


def _denial_facts(response: Response) -> tuple[str, str]:
    """(error_code, detail) of an already-rendered denial body, if it has one."""
    try:
        payload = json.loads(bytes(getattr(response, "body", b"") or b"{}"))
    except (TypeError, ValueError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    return str(payload.get("error_code") or ""), str(payload.get("detail") or "")


def attribute_request(
    request: Request, *, denied: Response | None, ledger: RefusalLedger | None
) -> None:
    """Name the caller for this request's telemetry, and ledger a refusal.

    Call this BEFORE ``call_next``: the identity is a contextvar, and only the
    layers entered after it is bound — routes, the tool dispatcher, the ledger
    itself — inherit it. A request the auth boundary short-circuits never
    reaches the dispatcher, so this is its only chance at the durable record;
    source and project scope come off the path, since no tool contract resolved
    to supply them.
    """
    principal = getattr(request.state, "principal", None)  # unset on OPTIONS
    # A refusal that never authenticated still leaves the local sentinel on
    # request.state; that is the default, not evidence of a local caller.
    unnamed = denied is not None and not getattr(request.state, "authenticated", False)
    bind_principal(principal_id="open" if unnamed else principal_label(principal))
    if denied is None or ledger is None:
        return
    path = request.url.path
    match = _PROJECT_PATH_RE.match(path)
    error_code, detail = _denial_facts(denied)
    ledger.reject(
        source="mcp" if path == "/mcp" or path.startswith("/mcp/") else "http",
        error_code=error_code or f"http_{denied.status_code}",
        error=detail or f"request refused with {denied.status_code}",
        project_id=match.group(1) if match else (request.query_params.get("project_id") or ""),
    )


# Global mutators/aggregates gated behind the operator token in hosted mode.
GLOBAL_MUTATOR_PREFIXES = ("/api/admin", "/api/debug/tool-calls/clear")


def _operator_token_ok(request: Request) -> bool:
    token = env_value(ADMIN_TOKEN_ENV_VAR) or ""
    supplied = request.headers.get(ADMIN_TOKEN_HEADER, "")
    return bool(token) and hmac.compare_digest(supplied, token)


def operator_denial(request: Request) -> JSONResponse | None:
    """Gate a GLOBAL operator mutator/aggregate (INV-11 FIX 1). LOCAL_PRINCIPAL
    (local mode, no verifier) is the trusted operator and keeps access; any
    hosted caller — even a JWT owner — must present MERV_ADMIN_TOKEN on the
    X-Admin-Token header (constant-time). An unset token in hosted mode denies
    everyone, so the prod cleanup cron must send the token."""
    if is_local_principal(getattr(request.state, "principal", None)):
        return None
    if _operator_token_ok(request):
        return None
    return JSONResponse(
        {"detail": "operator token required", "error_code": "operator_forbidden"},
        status_code=403,
    )


def open_hosted_operator_denial(request: Request) -> JSONResponse | None:
    """Operator gate for hosted control mode WITHOUT a verifier (OPEN mode).

    Open mode has no trusted principal — downstream code labels callers
    LOCAL_PRINCIPAL — so global mutators require the operator token
    unconditionally; there is no local bypass here.
    """
    if not request.url.path.startswith(GLOBAL_MUTATOR_PREFIXES):
        return None
    if _operator_token_ok(request):
        return None
    return JSONResponse(
        {"detail": "operator token required", "error_code": "operator_forbidden"},
        status_code=403,
    )

"""Shared shaping, scrubbing, and sizing helpers for in-memory telemetry."""

from __future__ import annotations

from contextlib import suppress
import json
import re
import time
from typing import Any

# Cap the per-event result payload written to the log. Tool results such as
# experiment.get_state and the project home view can be many KB; logging them
# verbatim on every call — including frequent UI polls — is what drives
# multi-hundred-MB/day growth. The log is a visibility feed, not an archive.
RESULT_LOG_MAX_BYTES = 16 * 1024

SENSITIVE_KEYS = {
    "reviewer_capability",
    "capability",
    "MLFLOW_TRACKING_PASSWORD",
}
LEGACY_MACHINE_LOCAL_KEYS = {"repo_root", "local_sync_dir", "local_experiment_dir"}

# Value-level secret scrubbing (INV-12). storage.submit/fetch AND feed.post
# results carry a one-time upload-token URL inside their `run` command string
# (storage also carries a presigned S3 URL — a ~1-hour replayable credential
# that bypasses brain auth entirely). Neither may reach a persisted log even
# when embedded in a string value, so we drop every SigV4 query param (name and
# value) and the upload-token path segments. The path set MUST stay in lockstep
# with shared._UPLOAD_TOKEN_PATH_RE (the HTTP access-log scrubber).
_S3_SIGV4_PARAM_RE = re.compile(
    r"(?i)X-Amz-(?:Signature|Credential|Security-Token|Algorithm|Date|Expires|SignedHeaders)=[^&'\"\s]+"
)
_UPLOAD_TOKEN_URL_RE = re.compile(
    r"(/api/(?:artifacts/[uf]|feed/u|storage/u)/)[^/?'\"\s]+"
)


def scrub_secret_text(text: str) -> str:
    """Redact presigned-URL SigV4 params and upload-token path segments embedded
    in a string value before it is persisted to a visibility log."""
    if "X-Amz-" in text:
        text = _S3_SIGV4_PARAM_RE.sub("<redacted>", text)
    if "/api/" in text:
        text = _UPLOAD_TOKEN_URL_RE.sub(r"\1<redacted>", text)
    return text
ID_KEYS = {
    "project_id",
    "claim_id",
    "experiment_id",
    "artifact_id",
    "review_request_id",
    "review_session_id",
    "job_id",
    "target_type",
    "target_id",
    "role",
    "transition",
    "verdict",
}


class ToolActivityEmitter:
    """Shared tool-call event shaping for activity sinks."""

    def tool_ok(
        self,
        *,
        source: str,
        tool: str,
        arguments: dict[str, Any],
        duration_ms: int,
        result: dict[str, Any],
    ) -> None:
        self.emit(
            event_type="tool.call",
            payload={
                "source": source,
                "tool": tool,
                "status": "ok",
                "duration_ms": duration_ms,
                "args": summarize_arguments(arguments=arguments),
                "result": cap_result(value=result),
                # Full I/O sizes in characters — what the agent actually sent and
                # received — independent of the capped `result`/summarized `args`
                # above. `received_chars` matches HTTP MCP serialization
                # (json.dumps(result, sort_keys=True)) so it reflects the exact
                # payload that lands in the agent's context. This is the signal
                # the debug view sorts on to find context-bloating tools.
                "sent_chars": payload_chars(value=arguments),
                "received_chars": payload_chars(value=result),
            },
        )

    def tool_error(
        self,
        *,
        source: str,
        tool: str,
        arguments: dict[str, Any],
        duration_ms: int,
        error: str,
        error_code: str = "",
    ) -> None:
        self.emit(
            event_type="tool.call",
            payload={
                "source": source,
                "tool": tool,
                "status": "error",
                "duration_ms": duration_ms,
                "error": error,
                "error_code": error_code,
                "args": summarize_arguments(arguments=arguments),
                "sent_chars": payload_chars(value=arguments),
                "received_chars": len(error or ""),
            },
        )


def effective_source(*, event: dict[str, Any]) -> str:
    """Treat http.request events as having an implicit source = http."""
    if event.get("event") == "http.request":
        return "http"
    return event.get("source") or "mcp"


def is_event_ok(*, event: dict[str, Any]) -> bool:
    if event.get("event") == "http.request":
        status = event.get("status")
        return not (isinstance(status, int) and status >= 400)
    status = event.get("status")
    return status in (None, "ok")


def summarize_arguments(*, arguments: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in SENSITIVE_KEYS:
            summary[key] = "[redacted]"
        elif key in ID_KEYS:
            summary[key] = value
    return summary


def payload_chars(*, value: Any) -> int:
    """Length (in chars) of a value serialized the way the agent sees it.

    Matches HTTP MCP's `json.dumps(result, sort_keys=True)` so the count is
    the true size of the JSON text that enters the agent's context. Returns 0 on
    any serialization failure rather than raising — this is telemetry.
    """
    try:
        return len(json.dumps(jsonable(value=value), sort_keys=True))
    except (TypeError, ValueError):
        return 0


def cap_result(*, value: Any) -> Any:
    """Return a JSON-safe result capped to RESULT_LOG_MAX_BYTES.

    Oversized results are replaced with a compact truncation marker so the
    activity log stays bounded. The caller still received the full result; the
    log is a visibility feed, not an archive.
    """
    safe = redact_sensitive(value=jsonable(value=value))
    try:
        encoded = json.dumps(safe, separators=(",", ":"))
    except (TypeError, ValueError):
        return safe
    if len(encoded) <= RESULT_LOG_MAX_BYTES:
        return safe
    return {
        "_truncated": True,
        "_bytes": len(encoded),
        "preview": encoded[:2048],
    }


def jsonable(*, value: Any) -> Any:
    with suppress(TypeError, ValueError):
        json.dumps(value)
        return value
    if isinstance(value, dict):
        return {str(key): jsonable(value=item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(value=item) for item in value]
    return str(value)


def redact_sensitive(*, value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if key in SENSITIVE_KEYS
            else redact_sensitive(value=item)
            for key, item in value.items()
            if key not in LEGACY_MACHINE_LOCAL_KEYS
        }
    if isinstance(value, list):
        return [redact_sensitive(value=item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(value=item) for item in value)
    if isinstance(value, str):
        return scrub_secret_text(value)
    return value


def monotonic_ms() -> int:
    return int(time.perf_counter() * 1000)

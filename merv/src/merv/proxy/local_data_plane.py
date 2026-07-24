"""Proxy-local execution for split-mode data-plane tools.

The MCP proxy runs on the user's machine, so it can safely perform the local
file reads and validation required by the current architecture. Proxy-local and
shared helpers are imported lazily inside methods to keep startup light.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from .routing import local_handler_identity


ControlToolCall = Callable[[str, dict[str, Any]], dict[str, Any]]
# Runtime-evaluated alias: typing.Optional, not `str | None` — the proxy must
# import under Apple CLT Python 3.9, where PEP 604 unions raise at runtime.
ProjectIdResolver = Callable[[], Optional[str]]


class LocalDataPlaneError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "validation_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


class LocalDataPlane:
    def __init__(
        self,
        *,
        repo_root: Path,
        project_id_resolver: ProjectIdResolver,
        control_tool_call: ControlToolCall,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self._project_id_resolver = project_id_resolver
        self._control_tool_call = control_tool_call

    def call_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        control_facts: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        arguments = dict(arguments or {})
        identity = local_handler_identity(name)
        if identity.startswith("local."):
            handler = getattr(self, f"_{identity.split('.', 1)[1]}", None)
            if handler is not None:
                if control_facts is not None:
                    return handler(arguments=arguments, control_facts=control_facts)
                return handler(arguments=arguments)
        raise LocalDataPlaneError(
            f"tool is not served by the proxy data plane: {name}",
            details={"tool": name},
        )

    def _health(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        return {"ok": True, "mode": "proxy"}

    def _sandbox_get_enrichment(
        self,
        *,
        arguments: dict[str, Any],
        control_facts: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        facts = control_facts
        if facts is None:
            args = dict(arguments)
            args["project_id"] = self._project_id()
            facts = self._control_tool_call("sandbox.get", args)
        sandbox_uid = str(facts.get("sandbox_uid") or "")
        experiment_id = str(facts.get("experiment_id") or sandbox_uid)
        name = f"sandbox-{sandbox_uid[:12]}" if sandbox_uid else ""
        local_dir = ""
        if experiment_id:
            local_dir = str(
                self._local_experiment_dir(experiment_id=experiment_id, name=name)
            )
        return {"local_dir": local_dir}

    # feed.post is a control tool since the no-dataplane transition (Phase D.1):
    # media bytes travel over the agent's own `curl -T` against the token-bearer
    # PUT /api/feed/u/<token>, so the proxy forwards feed.post to /mcp unchanged
    # and carries no feed media handler.


    def _project_id(self) -> str:
        project_id = self._project_id_resolver()
        if isinstance(project_id, str) and project_id:
            return project_id
        raise LocalDataPlaneError(
            "no hosted project link found for repo; call the project tool with "
            'action="connect" to link this folder to a project',
            error_code="project_not_linked",
            details={"repo_root": str(self.repo_root)},
        )

    def _local_experiment_dir(self, *, experiment_id: str, name: str = "") -> Path:
        from .workspace import local_experiment_dir

        return local_experiment_dir(
            repo_root=self.repo_root, experiment_id=experiment_id, name=name
        )

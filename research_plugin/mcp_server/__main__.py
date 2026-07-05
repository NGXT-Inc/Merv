"""Entrypoint for the Research Plugin MCP stdio proxy.

The MCP process is a thin adapter. In local mode it forwards every call to the
local HTTP backend; in split mode it performs local data-plane file reads itself
and forwards only explicit project-scoped facts/bytes to the control plane.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from research_plugin_shared.client_config import (
    DAEMON_SECRET_FILE_NAME,
    CLIENT_CONFIG_ENV_VAR,
    read_client_config,
    read_secret_file,
    resolve_client_config_path,
)

from .daemon_marker import discover_daemon_url
from .project_links import ProjectLinks, default_project_links_path
from .proxy import DEFAULT_DAEMON_URL, HttpProxyMcpServer, ProxyConfig


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="research-plugin-mcp",
        description="Stdio MCP proxy for the research_plugin HTTP daemon.",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("RESEARCH_PLUGIN_REPO_ROOT", "."),
        help="Research repo whose .research_plugin/daemon.json should be read.",
    )
    parser.add_argument(
        "--daemon-url",
        default=os.environ.get("RESEARCH_PLUGIN_DAEMON_URL"),
        help="Override the daemon URL (host:port). If unset, discovery uses the repo marker.",
    )
    parser.add_argument(
        "--control-url",
        default=os.environ.get("RESEARCH_PLUGIN_CONTROL_URL"),
        help="Cloud control-plane URL (split mode). Unset ⇒ single-upstream local mode.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    client_config_path = resolve_client_config_path()
    client_config = read_client_config({CLIENT_CONFIG_ENV_VAR: str(client_config_path)})
    project_links_path = default_project_links_path(
        client_config=client_config,
        config_path=client_config_path,
    )
    linked_client_config = (
        client_config
        if _repo_is_linked(db_path=project_links_path, repo_root=repo_root)
        else {}
    )
    control_url = (
        args.control_url
        or linked_client_config.get("control_url", "")
        or client_config.get("control_url", "")
    ).rstrip("/") or None
    daemon_url = None
    daemon_secret = None
    if not control_url:
        daemon_url = (
            args.daemon_url
            or discover_daemon_url(repo_root=repo_root)
            or linked_client_config.get("daemon_url", "")
            or client_config.get("daemon_url", "")
        )
        # The daemon loopback secret protects the local daemon API and is read
        # from a file so it never sits in a process arg that gets logged.
        daemon_secret_file = (
            os.environ.get("RESEARCH_PLUGIN_DAEMON_SECRET_FILE")
            or linked_client_config.get("daemon_secret_file")
            or client_config.get("daemon_secret_file")
        )
        if daemon_url and not daemon_secret_file:
            daemon_secret_file = str(
                Path.home() / ".research_plugin" / DAEMON_SECRET_FILE_NAME
            )
        daemon_secret = read_secret_file(daemon_secret_file, keys=("token", "secret"))

    if not daemon_url and not control_url:
        # Don't hard-exit: Codex will call initialize before anything else, and
        # the daemon may come up between launches. The proxy returns a clear
        # error envelope per tool call if the daemon is still missing then.
        sys.stderr.write(
            "[research_plugin] no HTTP daemon detected; tool calls will fail "
            f"until you start one with `research-plugin-http` at {DEFAULT_DAEMON_URL} "
            "or set RESEARCH_PLUGIN_DAEMON_URL to the shared daemon URL.\n"
        )

    config = ProxyConfig(
        repo_root=repo_root,
        daemon_url=daemon_url,
        control_url=control_url,
        daemon_secret=daemon_secret,
        project_links_path=project_links_path,
    )
    HttpProxyMcpServer(config=config).serve()
    return 0


def _repo_is_linked(*, db_path: Path, repo_root: Path) -> bool:
    try:
        return bool(ProjectLinks(db_path=db_path).project_for_repo(repo_root=str(repo_root)))
    except Exception:  # noqa: BLE001 - corrupt link DB should not kill initialize.
        return False


if __name__ == "__main__":
    raise SystemExit(main())

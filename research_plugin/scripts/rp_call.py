#!/usr/bin/env python3
"""Tiny driver that calls Research Plugin MCP tools through the real proxy.

Reuses mcp_server.proxy (same routing/auth/version/project_id logic the Claude
Code MCP client would use), so this is a faithful stand-in for the not-loaded
MCP server. Usage:

    rp_call.py list                       # tools/list (names + planes)
    rp_call.py schema <tool>              # full input schema for one tool
    rp_call.py call <tool> '<json-args>'  # tools/call, prints the result JSON

Env: RESEARCH_PLUGIN_REPO_ROOT picks the project working dir (defaults to CWD).
Control URL / daemon secret are read from ~/.research_plugin/client.json.
"""
from __future__ import annotations

import json
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
from mcp_server.daemon_marker import discover_daemon_url
from mcp_server.proxy import HttpProxyMcpServer, ProxyConfig


def _build_server() -> HttpProxyMcpServer:
    repo_root = Path(os.environ.get("RESEARCH_PLUGIN_REPO_ROOT", ".")).resolve()
    cfg_path = resolve_client_config_path()
    cc = read_client_config({CLIENT_CONFIG_ENV_VAR: str(cfg_path)})
    control_url = (
        os.environ.get("RESEARCH_PLUGIN_CONTROL_URL") or cc.get("control_url", "")
    ).rstrip("/") or None
    daemon_url = (
        os.environ.get("RESEARCH_PLUGIN_DAEMON_URL")
        or discover_daemon_url(repo_root=repo_root)
        or cc.get("daemon_url", "")
    )
    secret_file = (
        os.environ.get("RESEARCH_PLUGIN_DAEMON_SECRET_FILE")
        or cc.get("daemon_secret_file")
        or str(Path.home() / ".research_plugin" / DAEMON_SECRET_FILE_NAME)
    )
    secret = read_secret_file(secret_file, keys=("token", "secret"))
    cfg = ProxyConfig(
        repo_root=repo_root,
        daemon_url=daemon_url,
        control_url=control_url,
        daemon_secret=secret,
    )
    return HttpProxyMcpServer(config=cfg)


def _rpc(server: HttpProxyMcpServer, method: str, params: dict) -> dict:
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return resp or {}


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    server = _build_server()
    cmd = args[0]
    if cmd == "list":
        resp = _rpc(server, "tools/list", {})
        tools = (resp.get("result") or {}).get("tools", [])
        for t in tools:
            plane = t.get("plane") or t.get("_plane") or "?"
            print(f"{t.get('name'):28} plane={plane}")
        print(f"\n[{len(tools)} tools]")
        return 0
    if cmd == "schema":
        resp = _rpc(server, "tools/list", {})
        tools = (resp.get("result") or {}).get("tools", [])
        for t in tools:
            if t.get("name") == args[1]:
                print(json.dumps(t, indent=2))
                return 0
        print(f"tool not found: {args[1]}", file=sys.stderr)
        return 1
    if cmd == "call":
        name = args[1]
        call_args = json.loads(args[2]) if len(args) > 2 and args[2] else {}
        resp = _rpc(server, "tools/call", {"name": name, "arguments": call_args})
        print(json.dumps(resp, indent=2, default=str))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

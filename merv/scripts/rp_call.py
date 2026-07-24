#!/usr/bin/env python3
"""Tiny driver that calls Merv tools through the HTTP MCP endpoint.

Usage:
    rp_call.py list
    rp_call.py schema <tool>
    rp_call.py call <tool> '<json-args>'

Set MERV_MCP_KEY to a project key. MERV_CONTROL_URL overrides the machine
client config; an unconfigured machine uses the hosted server.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from merv.shared.client_config import (
    CLIENT_CONFIG_ENV_VAR,
    HOSTED_CONTROL_URL,
    dual_env_value,
    read_client_config,
    resolve_client_config_path,
)


def _endpoint() -> str:
    config_path = resolve_client_config_path()
    config = read_client_config({CLIENT_CONFIG_ENV_VAR: str(config_path)})
    base_url = (
        dual_env_value("MERV_CONTROL_URL")
        or config.get("control_url")
        or HOSTED_CONTROL_URL
    )
    return f"{base_url.rstrip('/')}/mcp"


def _rpc(method: str, params: dict[str, object]) -> dict[str, object]:
    key = (os.environ.get("MERV_MCP_KEY") or "").strip()
    if not key:
        raise RuntimeError("MERV_MCP_KEY is required")
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    request = urllib.request.Request(
        _endpoint(),
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _tools() -> list[dict[str, object]]:
    response = _rpc("tools/list", {})
    result = response.get("result")
    return list(result.get("tools", [])) if isinstance(result, dict) else []


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    try:
        command = args[0]
        if command == "list":
            tools = _tools()
            for tool in tools:
                print(str(tool.get("name") or ""))
            print(f"\n[{len(tools)} tools]")
            return 0
        if command == "schema":
            if len(args) < 2:
                raise RuntimeError("schema requires a tool name")
            for tool in _tools():
                if tool.get("name") == args[1]:
                    print(json.dumps(tool, indent=2))
                    return 0
            print(f"tool not found: {args[1]}", file=sys.stderr)
            return 1
        if command == "call":
            if len(args) < 2:
                raise RuntimeError("call requires a tool name")
            arguments = json.loads(args[2]) if len(args) > 2 and args[2] else {}
            response = _rpc(
                "tools/call",
                {"name": args[1], "arguments": arguments},
            )
            print(json.dumps(response, indent=2, default=str))
            return 0
        print(f"unknown command: {command}", file=sys.stderr)
        return 2
    except (RuntimeError, ValueError) as exc:
        print(f"rp_call.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

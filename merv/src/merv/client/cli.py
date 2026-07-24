"""Onboarding CLI for the universal HTTP MCP endpoint."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from merv.shared.client_config import (
    CLIENT_CONFIG_ENV_VAR,
    CONTROL_URL_ENV_VAR,
    HOSTED_CONTROL_URL,
    LOCAL_BRAIN_URL,
    dual_env_value,
    read_client_config,
    resolve_client_config_path,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ClientError as exc:
        print(f"merv-client: {exc}", file=sys.stderr)
        return 2


class ClientError(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="merv-client",
        description="Configure Merv and print an HTTP MCP client snippet.",
    )
    parser.add_argument(
        "--config",
        help=(
            "Machine client config path (default: ~/.merv/client.json, or the "
            "legacy ~/.research_plugin/client.json when that dir exists)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    configure = sub.add_parser(
        "configure",
        help="Save the Merv server URL for this machine.",
    )
    _add_control_url_arg(configure)
    configure.set_defaults(func=_cmd_configure)

    env = sub.add_parser(
        "env",
        help="Print the .mcp.json HTTP server snippet.",
    )
    env.set_defaults(func=_cmd_env)
    return parser


def _add_control_url_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--control-url",
        default=HOSTED_CONTROL_URL,
        help=(
            f"Merv server URL (default: {HOSTED_CONTROL_URL}; use "
            f"{LOCAL_BRAIN_URL} for a local deployment)."
        ),
    )


def _cmd_configure(args: argparse.Namespace) -> int:
    config_path = _config_path(args)
    config = configure_client(
        config_path=config_path,
        control_url=args.control_url,
    )
    print(f"configured machine client: {config_path}")
    print(f"control_url={config['control_url']}")
    return 0


def _cmd_env(args: argparse.Namespace) -> int:
    config_path = _config_path(args)
    config = read_client_config({CLIENT_CONFIG_ENV_VAR: str(config_path)})
    control_url = (
        dual_env_value(CONTROL_URL_ENV_VAR)
        or config.get("control_url")
        or HOSTED_CONTROL_URL
    ).rstrip("/")
    snippet = {
        "mcpServers": {
            "merv": {
                "type": "http",
                "url": f"{control_url}/mcp",
                "headers": {
                    "Authorization": "Bearer ${MERV_MCP_KEY}",
                },
            },
        },
    }
    print(json.dumps(snippet, indent=2))
    return 0


def configure_client(*, config_path: Path, control_url: str) -> dict[str, str]:
    normalized = (control_url or HOSTED_CONTROL_URL).strip().rstrip("/")
    if not normalized:
        raise ClientError("control_url is required")
    config = {"control_url": normalized}
    _write_json_private(config_path, config)
    return config


def _config_path(args: argparse.Namespace) -> Path:
    if getattr(args, "config", None):
        return Path(args.config).expanduser().resolve()
    return resolve_client_config_path()


def _write_json_private(path: Path, payload: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


if __name__ == "__main__":
    raise SystemExit(main())

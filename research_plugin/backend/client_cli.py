"""Machine-level CLI for hosted-control + local-data split mode."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from research_plugin_shared.client_config import (
    CLIENT_CONFIG_ENV_VAR,
    DAEMON_STATE_DIR_ENV_VAR,
    DAEMON_SECRET_FILE_NAME,
    DEFAULT_DAEMON_URL,
    read_client_config,
    read_secret_file,
    resolve_client_config_path,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
PID_FILE_NAME = "daemon.pid"
LOG_FILE_NAME = "daemon.log"


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ClientError as exc:
        print(f"research-plugin-client: {exc}", file=sys.stderr)
        return 2


class ClientError(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-plugin-client",
        description="Configure a machine data-plane daemon and link local folders to hosted Research Plugin projects.",
    )
    parser.add_argument(
        "--config",
        help="Machine client config path (default: ~/.research_plugin/client.json).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    configure = sub.add_parser(
        "configure",
        aliases=["login"],
        help="Save hosted-control URL for this machine.",
    )
    _add_control_args(configure)
    configure.set_defaults(func=_cmd_configure)

    start = sub.add_parser("start", help="Start the machine data-plane daemon.")
    _add_daemon_args(start)
    start.add_argument("--restart", action="store_true", help="Stop an existing daemon first.")
    start.add_argument("--no-wait", action="store_true", help="Do not wait for loopback health.")
    start.set_defaults(func=_cmd_start)

    stop = sub.add_parser("stop", help="Stop the daemon started by this client.")
    stop.set_defaults(func=_cmd_stop)

    health = sub.add_parser("health", help="Check daemon and cloud reachability.")
    health.set_defaults(func=_cmd_health)

    link = sub.add_parser(
        "link",
        help="Link a local repo folder to an existing hosted project_id.",
    )
    link.add_argument("--project-id", required=True, help="Hosted control-plane project id.")
    link.add_argument("--repo", default=".", help="Local repo folder to link (default: cwd).")
    link.add_argument("--no-start", action="store_true", help="Do not auto-start the daemon.")
    link.set_defaults(func=_cmd_link)

    connect = sub.add_parser(
        "connect",
        help="Configure/login, start the daemon, and optionally link the current folder.",
    )
    _add_control_args(connect, required=False)
    _add_daemon_args(connect)
    connect.add_argument("--restart", action="store_true", help="Stop an existing daemon first.")
    connect.add_argument("--no-wait", action="store_true", help="Do not wait for loopback health.")
    connect.add_argument("--project-id", help="Existing hosted project id to link.")
    connect.add_argument("--repo", default=".", help="Local repo folder to link (default: cwd).")
    connect.set_defaults(func=_cmd_connect)

    route = sub.add_parser("route", help="Show the project linked to a local repo folder.")
    route.add_argument("--repo", default=".", help="Local repo folder (default: cwd).")
    route.set_defaults(func=_cmd_route)

    links = sub.add_parser("links", help="List local folder links on this machine.")
    links.set_defaults(func=_cmd_links)

    unlink = sub.add_parser("unlink", help="Remove one local folder link.")
    unlink.add_argument("--repo", default=".", help="Local repo folder to unlink (default: cwd).")
    unlink.set_defaults(func=_cmd_unlink)

    env = sub.add_parser(
        "mcp-env",
        help="Print environment variables a manual MCP config should use.",
    )
    env.add_argument("--repo", default=".", help="Local repo folder (default: cwd).")
    env.set_defaults(func=_cmd_mcp_env)
    return parser


def _add_control_args(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument(
        "--control-url",
        required=required,
        help="Hosted control-plane URL, e.g. https://experiments.rapidreview.io.",
    )


def _add_daemon_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host",
        help="Daemon loopback host (default: configured host or 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Daemon loopback port (default: configured port or 8787).",
    )
    parser.add_argument(
        "--daemon-command",
        help="Daemon launcher path (default: bundled bin/research-plugin-daemon or PATH).",
    )


def _cmd_configure(args: argparse.Namespace) -> int:
    config_path = _config_path(args)
    existing = read_client_config({CLIENT_CONFIG_ENV_VAR: str(config_path)})
    config = configure_client(
        config_path=config_path,
        control_url=args.control_url,
        daemon_url=existing.get("daemon_url", DEFAULT_DAEMON_URL),
    )
    _print_configured(config_path=config_path, config=config)
    return 0


def _cmd_connect(args: argparse.Namespace) -> int:
    config_path = _config_path(args)
    existing = read_client_config({CLIENT_CONFIG_ENV_VAR: str(config_path)})
    host, port = _daemon_endpoint(config=existing, args=args)
    if _has_control_config_args(args):
        control_url = args.control_url or existing.get("control_url", "")
        config = configure_client(
            config_path=config_path,
            control_url=control_url,
            daemon_url=_daemon_url(host, port),
        )
        _print_configured(config_path=config_path, config=config)
    _cmd_start(args)
    if args.project_id:
        repo = _repo(args.repo)
        link_repo(
            config_path=config_path,
            repo_root=repo,
            project_id=args.project_id,
        )
        print(f"linked {repo} -> {args.project_id}")
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    config_path = _config_path(args)
    if args.restart:
        stop_daemon(config_path=config_path)
    config = _require_config(config_path)
    host, port = _daemon_endpoint(config=config, args=args)
    config["daemon_url"] = _daemon_url(host, port)
    _write_json_private(config_path, config)
    current = daemon_health(config_path=config_path, quiet=True)
    if current.get("ok"):
        if not _daemon_ready(current):
            raise ClientError(f"daemon is running but hosted control is not reachable: {current}")
        print(f"daemon already running at {config['daemon_url']}")
        return 0
    pid = start_daemon(
        config_path=config_path,
        host=host,
        port=port,
        daemon_command=args.daemon_command,
    )
    print(f"started daemon pid {pid} at {config['daemon_url']}")
    if not args.no_wait:
        deadline = time.monotonic() + 20.0
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = daemon_health(config_path=config_path, quiet=True)
            if _daemon_ready(last):
                print("daemon healthy")
                return 0
            time.sleep(0.5)
        stop_daemon(config_path=config_path)
        raise ClientError(f"daemon did not become healthy; last status: {last}")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    stopped = stop_daemon(config_path=_config_path(args))
    print("daemon stopped" if stopped else "daemon was not running")
    return 0


def _cmd_health(args: argparse.Namespace) -> int:
    status = daemon_health(config_path=_config_path(args), quiet=False)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0 if _daemon_ready(status) else 1


def _cmd_link(args: argparse.Namespace) -> int:
    config_path = _config_path(args)
    if not args.no_start and not daemon_health(config_path=config_path, quiet=True).get("ok"):
        start_args = argparse.Namespace(
            config=args.config,
            host=None,
            port=None,
            daemon_command=None,
            restart=False,
            no_wait=False,
        )
        _cmd_start(start_args)
    repo = _repo(args.repo)
    link_repo(config_path=config_path, repo_root=repo, project_id=args.project_id)
    print(f"linked {repo} -> {args.project_id}")
    return 0


def _cmd_route(args: argparse.Namespace) -> int:
    route = route_repo(config_path=_config_path(args), repo_root=_repo(args.repo))
    print(json.dumps(route, indent=2, sort_keys=True))
    return 0 if route.get("exists") else 1


def _cmd_links(args: argparse.Namespace) -> int:
    links = list_links(config_path=_config_path(args))
    print(json.dumps(links, indent=2, sort_keys=True))
    return 0


def _cmd_unlink(args: argparse.Namespace) -> int:
    result = unlink_repo(config_path=_config_path(args), repo_root=_repo(args.repo))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_mcp_env(args: argparse.Namespace) -> int:
    config_path = _config_path(args)
    config = _require_config(config_path)
    repo = _repo(args.repo)
    env = {
        "RESEARCH_PLUGIN_REPO_ROOT": str(repo),
        "RESEARCH_PLUGIN_DAEMON_URL": config["daemon_url"],
        "RESEARCH_PLUGIN_CONTROL_URL": config["control_url"],
        "RESEARCH_PLUGIN_DAEMON_SECRET_FILE": config["daemon_secret_file"],
        DAEMON_STATE_DIR_ENV_VAR: config["daemon_state_dir"],
        CLIENT_CONFIG_ENV_VAR: str(config_path),
    }
    for key, value in env.items():
        print(f"{key}={value}")
    return 0


def configure_client(
    *,
    config_path: Path,
    control_url: str,
    daemon_url: str,
) -> dict[str, str]:
    if not control_url:
        raise ClientError("--control-url is required until this machine is configured")
    existing = read_client_config({CLIENT_CONFIG_ENV_VAR: str(config_path)})
    config_path.parent.mkdir(parents=True, exist_ok=True)
    daemon_state_dir = Path(
        existing.get("daemon_state_dir") or config_path.parent
    ).expanduser().resolve()
    config = {
        "control_url": control_url.rstrip("/"),
        "daemon_url": daemon_url.rstrip("/"),
        "daemon_secret_file": existing.get(
            "daemon_secret_file", str(daemon_state_dir / DAEMON_SECRET_FILE_NAME)
        ),
        "daemon_state_dir": str(daemon_state_dir),
    }
    _write_json_private(config_path, config)
    return config


def start_daemon(
    *,
    config_path: Path,
    host: str,
    port: int,
    daemon_command: str | None = None,
) -> int:
    config = _require_config(config_path)
    command = _daemon_command(daemon_command)
    state_dir = _state_dir(config_path=config_path, config=config)
    log_path = state_dir / LOG_FILE_NAME
    pid_path = state_dir / PID_FILE_NAME
    state_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            CLIENT_CONFIG_ENV_VAR: str(config_path),
            DAEMON_STATE_DIR_ENV_VAR: str(state_dir),
            "RESEARCH_PLUGIN_CONTROL_URL": config["control_url"],
            "RESEARCH_PLUGIN_DAEMON_URL": _daemon_url(host, port),
        }
    )
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            [str(command), "--host", host, "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    pid_path.chmod(0o600)
    return int(proc.pid)


def stop_daemon(*, config_path: Path) -> bool:
    pid_path = _state_dir(
        config_path=config_path,
        config=read_client_config({CLIENT_CONFIG_ENV_VAR: str(config_path)}),
    ) / PID_FILE_NAME
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if not _pid_alive(pid):
        _unlink_quietly(pid_path)
        return False
    if not _pid_looks_like_daemon(pid):
        _unlink_quietly(pid_path)
        return False
    stopped = False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
            stopped = True
        except ProcessLookupError:
            stopped = True
            break
        except OSError:
            break
        time.sleep(0.5)
        if not _pid_alive(pid):
            break
    _unlink_quietly(pid_path)
    return stopped


def daemon_health(*, config_path: Path, quiet: bool) -> dict[str, Any]:
    try:
        return _daemon_request(config_path=config_path, method="GET", path="/health")
    except ClientError as exc:
        if quiet:
            return {"ok": False, "error": str(exc)}
        raise


def link_repo(*, config_path: Path, repo_root: Path, project_id: str) -> dict[str, Any]:
    if not project_id:
        raise ClientError("project_id is required")
    return _daemon_request(
        config_path=config_path,
        method="POST",
        path="/local/link",
        payload={"repo_root": str(repo_root), "project_id": project_id},
    )


def route_repo(*, config_path: Path, repo_root: Path) -> dict[str, Any]:
    query = urlencode({"repo_root": str(repo_root)})
    return _daemon_request(config_path=config_path, method="GET", path=f"/local/route?{query}")


def list_links(*, config_path: Path) -> dict[str, Any]:
    return _daemon_request(config_path=config_path, method="GET", path="/local/links")


def unlink_repo(*, config_path: Path, repo_root: Path) -> dict[str, Any]:
    query = urlencode({"repo_root": str(repo_root)})
    return _daemon_request(config_path=config_path, method="DELETE", path=f"/local/link?{query}")


def _daemon_request(
    *,
    config_path: Path,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = _require_config(config_path)
    secret = _read_daemon_secret(config)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {secret}",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    url = config.get("daemon_url", DEFAULT_DAEMON_URL).rstrip("/") + path
    req = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=5.0) as response:
            raw = response.read()
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ClientError(f"daemon rejected {method} {path}: HTTP {exc.code} {detail}") from exc
    except urllib_error.URLError as exc:
        raise ClientError(f"daemon is not reachable at {url}: {exc.reason}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ClientError(f"daemon returned non-JSON response from {path}") from exc
    if not isinstance(parsed, dict):
        raise ClientError(f"daemon returned invalid response from {path}")
    return parsed


def _config_path(args: argparse.Namespace) -> Path:
    if getattr(args, "config", None):
        return Path(args.config).expanduser().resolve()
    return resolve_client_config_path()


def _require_config(config_path: Path) -> dict[str, str]:
    config = read_client_config({CLIENT_CONFIG_ENV_VAR: str(config_path)})
    missing = [key for key in ("control_url",) if not config.get(key)]
    if missing:
        raise ClientError(
            "machine is not configured; run "
            "research-plugin-client configure --control-url ..."
        )
    config.setdefault("daemon_url", DEFAULT_DAEMON_URL)
    state_dir = str(_state_dir(config_path=config_path, config=config))
    config.setdefault("daemon_state_dir", state_dir)
    config.setdefault(
        "daemon_secret_file", str(Path(state_dir).expanduser() / DAEMON_SECRET_FILE_NAME)
    )
    return config


def _state_dir(*, config_path: Path, config: Mapping[str, str]) -> Path:
    raw = (config.get("daemon_state_dir") or "").strip()
    return Path(raw).expanduser().resolve() if raw else config_path.parent


def _has_control_config_args(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "control_url", None))


def _write_json_private(path: Path, payload: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _read_daemon_secret(config: Mapping[str, str]) -> str:
    raw = config.get("daemon_secret_file") or ""
    path = Path(raw).expanduser() if raw else Path.home() / ".research_plugin" / DAEMON_SECRET_FILE_NAME
    secret = read_secret_file(path, keys=("token", "secret"))
    if secret is None:
        raise ClientError(f"daemon secret is missing at {path}; start the daemon first")
    if not secret:
        raise ClientError(f"daemon secret is empty at {path}")
    return secret


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _daemon_command(raw: str | None) -> Path:
    if raw:
        path = Path(raw).expanduser()
        if not path.exists():
            raise ClientError(f"daemon command does not exist: {path}")
        return path
    bundled = Path(__file__).resolve().parents[1] / "bin" / "research-plugin-daemon"
    if bundled.exists():
        return bundled
    for base in (Path(sys.argv[0]).resolve().parent, Path(sys.executable).resolve().parent):
        sibling = base / "research-plugin-daemon"
        if sibling.exists():
            return sibling
    found = shutil.which("research-plugin-daemon")
    if found:
        return Path(found)
    raise ClientError("research-plugin-daemon not found; install the daemon profile first")


def _daemon_url(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        return f"http://[{host}]:{int(port)}"
    return f"http://{host}:{int(port)}"


def _daemon_endpoint(*, config: Mapping[str, str], args: argparse.Namespace) -> tuple[str, int]:
    host = getattr(args, "host", None)
    port = getattr(args, "port", None)
    configured = str(config.get("daemon_url") or DEFAULT_DAEMON_URL)
    parsed = urlsplit(configured)
    if host is None:
        host = parsed.hostname or DEFAULT_HOST
    if port is None:
        port = parsed.port or DEFAULT_PORT
    _validate_loopback_host(str(host))
    return str(host), int(port)


def _validate_loopback_host(host: str) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ClientError(f"daemon host must be loopback-only, got {host!r}")


def _daemon_ready(status: Mapping[str, Any]) -> bool:
    return bool(status.get("ok") and status.get("cloud_reachable") is not False)


def _repo(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise ClientError(f"repo path does not exist: {path}")
    if not path.is_dir():
        raise ClientError(f"repo path is not a directory: {path}")
    return path


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_looks_like_daemon(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return False
    command = result.stdout
    return "backend.transport.http_server" in command or "research-plugin-daemon" in command


def _print_configured(*, config_path: Path, config: Mapping[str, str]) -> None:
    print(f"configured machine client: {config_path}")
    print(f"control_url={config['control_url']}")
    print(f"daemon_url={config['daemon_url']}")


if __name__ == "__main__":
    raise SystemExit(main())

"""HTTP process for the Merv brain server.

Owns the running uvicorn server: binds the socket and serves the FastAPI app
from the unified brain composition. Local deployment is just this server on
localhost with small-store defaults.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import uvicorn

from ..config import Mode, resolve_mode
from ...kernel.env import env_bool, env_value
from ...kernel.utils import ValidationError
from .http_api import create_fastapi_app
from .http_policy import HttpSurfacePolicy


def _bind_socket(*, host: str, port: int) -> socket.socket:
    bind_host = host or "127.0.0.1"
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    server_socket = socket.socket(family, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((bind_host, port))
    server_socket.listen(socket.SOMAXCONN)
    server_socket.set_inheritable(True)
    return server_socket


def is_loopback_host(host: str) -> bool:
    """Whether binding ``host`` can only be reached from this machine."""
    candidate = (host or "127.0.0.1").strip().strip("[]").lower()
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


class UvicornHttpServer:
    """uvicorn server wrapper used by compatibility tests.

    The production launcher builds the unified brain directly. This wrapper is
    a small socket/uvicorn harness for tests and programmatic callers.

    It is also a composition root, so it answers the same question every other
    one does: WHICH surface is this? Passing ``surface_policy`` threads the
    answer (with ``auth``/``env``) into ``create_fastapi_app``, where the
    hosted gate decides. Omitting it means the unauthenticated local default,
    which is only honest on a loopback bind — so a non-loopback host is refused
    here rather than quietly serving an open full surface off-machine.
    """

    def __init__(
        self,
        *,
        app: Any,
        host: str,
        port: int,
        surface_policy: HttpSurfacePolicy | None = None,
        auth: Any | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if surface_policy is None and not is_loopback_host(host):
            raise ValidationError(
                f"refusing to serve the unauthenticated local surface on {host!r}: "
                "bind a loopback address, or pass surface_policy (and auth) so "
                "the hosted authentication decision is made explicitly.",
                details={"host": host},
            )
        fastapi_app = create_fastapi_app(
            app=app.http, surface_policy=surface_policy, auth=auth, env=env
        )
        self._socket = _bind_socket(host=host, port=port)
        selected_port = int(self._socket.getsockname()[1])
        self.server_address = (host, selected_port)
        config = uvicorn.Config(
            fastapi_app,
            host=host,
            port=selected_port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(config)

    def serve_forever(self) -> None:
        self._server.run(sockets=[self._socket])

    def shutdown(self) -> None:
        self._server.should_exit = True

    def server_close(self) -> None:
        self._socket.close()


def make_http_server(
    app: Any,
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    surface_policy: HttpSurfacePolicy | None = None,
    auth: Any | None = None,
    env: Mapping[str, str] | None = None,
) -> UvicornHttpServer:
    return UvicornHttpServer(
        app=app,
        host=host,
        port=port,
        surface_policy=surface_policy,
        auth=auth,
        env=env,
    )


def _run_server(*, server: Any, host: str, port: int, label: str) -> int:
    server_socket = _bind_socket(host=host, port=port)
    selected_port = int(server_socket.getsockname()[1])
    config = uvicorn.Config(
        server.fastapi_app,
        host=host,
        port=selected_port,
        log_level="warning",
        access_log=False,
        lifespan="off",
        # Honor X-Forwarded-Proto/-For from the fronting proxy: the artifact
        # upload curls are minted from request.base_url and must say https.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    uv = uvicorn.Server(config)
    print(f"merv {label} listening on http://{host}:{selected_port}", flush=True)
    try:
        uv.run(sockets=[server_socket])
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server_socket.close()
    return 0


def _serve_control(*, host: str, port: int) -> int:
    """Run the hosted brain preset.

    Hosted/no-repo-root control requires durable DB, durable blob store, and a
    mounted management key. End-user auth is Supabase-backed and REQUIRED:
    booting without a verifier fails startup unless the operator sets
    MERV_ALLOW_OPEN_CONTROL=1, which serves an OPEN surface and says so in the
    boot log.
    """
    from ..composition import build_control_server

    server = build_control_server()
    return _run_server(server=server, host=host, port=port, label="CONTROL plane")


def _serve_local(*, host: str, port: int, state_dir: Path | None) -> int:
    """Run the localhost brain preset."""
    from ..composition import build_local_server

    server = build_local_server(state_dir=state_dir)
    return _run_server(server=server, host=host, port=port, label="brain")


def control_main() -> int:
    """Launch the hosted brain.

    The console-script entry for the ``control`` extra and the deploy Dockerfile:
    forces control mode (MERV_MODE=control) so the image entrypoint
    never accidentally binds the local preset. The expiry reaper runs, but the
    broader cleanup sweeps are only built; a managed cron or sidecar must POST
    ``/api/admin/cleanup``. End-user auth is Supabase verification and startup
    fails without it (MERV_ALLOW_OPEN_CONTROL=1 is the deliberate, loudly
    logged escape); deploy behind TLS and a trusted network boundary either way.
    """
    os.environ["MERV_MODE"] = "control"
    return main()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=env_value("MERV_HTTP_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=int(env_value("MERV_HTTP_PORT") or "8787"))
    parser.add_argument(
        "--registry-store",
        default=env_value("MERV_REGISTRY_STORE"),
        help=(
            "Compatibility path whose parent selects the local brain state "
            "root (research records live under the sibling brain/ directory). "
            "Unset lets the composition resolve ~/.merv/brain, or the legacy "
            "~/.research_plugin/brain when that state already exists."
        ),
    )
    parser.add_argument(
        "--activity-stderr",
        action="store_true",
        default=env_bool("MERV_ACTIVITY_STDERR", default=False),
        help=(
            "Legacy compatibility flag. The unified brain exposes bounded "
            "diagnostics over HTTP and does not mirror them to stderr."
        ),
    )
    args = parser.parse_args()

    mode = resolve_mode()
    if mode is Mode.CONTROL:
        return _serve_control(host=args.host, port=args.port)

    if args.activity_stderr:
        os.environ["MERV_ACTIVITY_STDERR"] = "1"
    return _serve_local(
        host=args.host,
        port=args.port,
        state_dir=(
            Path(args.registry_store).expanduser().resolve().parent / "brain"
            if args.registry_store
            else None
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())

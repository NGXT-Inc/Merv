"""Mode composition roots.

Mode is selected in composition only; services are mode-blind. One repo, two
process roles:

- ``local_mode``  — today's topology, both planes in one process (the default,
  byte-identical to before this phase).
- ``control_mode`` — the cloud control plane: record services + lifecycle +
  blob store + quotas.
  It serves /mcp/* (control tools) + /api/* but NEVER touches a user checkout.

``http_server.main`` dispatches on ``resolve_mode`` to the right builder. Each
builder owns its own fail-fast validation.
"""

from __future__ import annotations

from .control_mode import ControlPlaneServer, build_control_app, build_control_server
from .local_mode import build_local_app

__all__ = [
    "ControlPlaneServer",
    "build_control_app",
    "build_control_server",
    "build_local_app",
]

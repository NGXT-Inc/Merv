"""Central mode/config resolution for the backend.

The cloud split (docs/CLOUD_BACKEND_MIGRATION_PLAN.md) gives the backend three
process roles selected by ``RESEARCH_PLUGIN_MODE``:

- ``local``  — today's topology: one process binds the control plane and the
  data plane in-process (default, and the only mode implemented so far).
- ``control`` — cloud control plane (multi-tenant records, gates, lifecycle).
- ``daemon`` — slim local data-plane daemon (rsync, keys, file observation).

Mode resolution is fail-fast: an unknown value refuses to start rather than
silently running in the wrong topology. All later config (DB URLs, blob
backends, control URLs) hangs off this module so there is exactly one place
that decides what a process is.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import Enum

from .utils import ValidationError


MODE_ENV_VAR = "RESEARCH_PLUGIN_MODE"

# Modes the migration plan defines but later phases implement. Recognized so
# the error says "not yet implemented" instead of "unknown mode".
PLANNED_MODES = ("control", "daemon")


class Mode(str, Enum):
    LOCAL = "local"


def resolve_mode(env: Mapping[str, str] | None = None) -> Mode:
    """Resolve the process mode from the environment, failing fast."""
    source = env if env is not None else os.environ
    raw = (source.get(MODE_ENV_VAR) or "").strip().lower() or Mode.LOCAL.value
    if raw == Mode.LOCAL.value:
        return Mode.LOCAL
    if raw in PLANNED_MODES:
        raise ValidationError(
            f"RESEARCH_PLUGIN_MODE={raw!r} is not implemented yet; "
            "only 'local' is available (see docs/CLOUD_BACKEND_MIGRATION_PLAN.md)",
            details={"mode": raw},
        )
    raise ValidationError(
        f"unknown RESEARCH_PLUGIN_MODE: {raw!r} (expected 'local')",
        details={"mode": raw},
    )

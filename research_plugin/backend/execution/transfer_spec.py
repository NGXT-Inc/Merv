"""The shared transfer contract: excludes, size caps, and the parachute tar.

One constants module feeds every byte path (cloud plan Phase 5, §3.1):
``ssh_rsync`` builds its exclude/--max-size flags from these values, and the
expiry parachute (fixed decision 5) tars the experiment dir with the very
same rules — so what survives a reaped VM is exactly what a final pull would
have brought home. ``TRANSFER_CONTRACT_VERSION`` pins the rules into every
sync session (Phase 4); a session minted under different rules is refused by
the worker before any byte moves.

Scope invariant (pinned by tests): the parachute tars ``$RP_EXPERIMENT_DIR``
only. ``$RP_SANDBOX_DATA_DIR`` — including the ``.rp_runs/`` env dumps the
rec.sh supervisor writes there, which can carry HF_TOKEN — lives outside the
experiment dir and never rides the parachute, mirroring the sync scope.
"""

from __future__ import annotations

import fnmatch
import shlex
from pathlib import PurePosixPath

from .sync_dirs import ARTIFACTS_TO_KEEP_DIRNAME


# Bumping this version invalidates outstanding sync sessions (the worker
# refuses a mismatched pin) — bump it whenever the excludes or caps change.
TRANSFER_CONTRACT_VERSION = 1


DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".ipynb_checkpoints/",
    "node_modules/",
    ".cache/",
    "*.parquet",
    "*.arrow",
    "*.feather",
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.safetensors",
    "*.bin",
    "*.onnx",
    "*.h5",
    "*.npy",
    "*.npz",
    "*.zip",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
)

# Sandbox-authored telemetry (command transcripts, MLflow/TensorBoard stores
# and pid files). It lives OUTSIDE the remote experiment folder, but the push
# and the parachute both exclude it defensively: legacy local mirrors may
# contain an in-folder copy, and with --delete an --exclude *protects* the
# remote tree.
SESSIONS_DIR_EXCLUDE = ".research_plugin_sessions/"

# Per-file size caps, in rsync --max-size spelling (MiB-based suffixes).
# The experiment dir syncs at the general cap; artifacts_to_keep rides its
# own pass at the large-artifact cap. The parachute tar enforces the same
# pair via `find -size` skip lists.
SYNC_MAX_FILE_SIZE = "100m"
ARTIFACTS_MAX_FILE_SIZE = "5g"

# Everything the parachute tar must skip: the shared excludes plus the
# defensive sessions-dir pattern (same set the initial push excludes).
PARACHUTE_EXCLUDES: tuple[str, ...] = DEFAULT_EXCLUDES + (SESSIONS_DIR_EXCLUDE,)

# Receipt line the parachute script prints after a successful upload; the
# backends' run_parachute parses it into {sha256, size_bytes}.
PARACHUTE_RECEIPT_PREFIX = "RP_PARACHUTE "


def max_size_mib(spec: str) -> int:
    """Parse an rsync --max-size spelling ("100m", "5g") into whole MiB."""
    unit = spec[-1].lower()
    value = int(spec[:-1])
    if unit == "m":
        return value
    if unit == "g":
        return value * 1024
    raise ValueError(f"unsupported max-size spelling: {spec!r}")


def exclude_dir_names() -> tuple[str, ...]:
    """Directory-shaped exclude patterns (trailing slash), as bare names."""
    return tuple(p.rstrip("/") for p in PARACHUTE_EXCLUDES if p.endswith("/"))


def exclude_file_globs() -> tuple[str, ...]:
    """File-glob exclude patterns (everything that is not directory-shaped)."""
    return tuple(p for p in PARACHUTE_EXCLUDES if not p.endswith("/"))


def tar_exclude_args() -> tuple[str, ...]:
    """GNU tar --exclude args derived from the SAME patterns rsync uses.

    Directory patterns lose their trailing slash — GNU tar exclusion is
    unanchored, so a bare component name matches the directory at any depth
    and tar skips everything below it. File globs pass through unchanged.
    (The parachute always runs on Linux VMs; GNU tar semantics are the
    contract, pinned by the docker-simulated VM test.)
    """
    return tuple(f"--exclude={p.rstrip('/')}" for p in PARACHUTE_EXCLUDES)


def is_excluded_relpath(relpath: str) -> bool:
    """Python-side mirror of the exclude rules, for simulated VMs (the fake
    backend's parachute) and cross-checks: a path is excluded when any
    component is an excluded directory name or its basename matches a glob."""
    parts = PurePosixPath(relpath).parts
    if not parts:
        return False
    dir_names = set(exclude_dir_names())
    if any(part in dir_names for part in parts):
        return True
    return any(fnmatch.fnmatch(parts[-1], glob) for glob in exclude_file_globs())


def max_size_bytes_for(relpath: str) -> int:
    """The per-file byte cap a path gets under the shared contract."""
    parts = PurePosixPath(relpath).parts
    cap = (
        ARTIFACTS_MAX_FILE_SIZE
        if parts and parts[0] == ARTIFACTS_TO_KEEP_DIRNAME
        else SYNC_MAX_FILE_SIZE
    )
    return max_size_mib(cap) * 1024 * 1024


def build_parachute_script() -> str:
    """Render ``/opt/rp/parachute.sh`` (pre-installed by both bootstraps).

    Tars ``$RP_EXPERIMENT_DIR`` with the shared excludes and size caps and
    uploads the result with ``curl -T`` to its one argument — a single-use
    presigned PUT URL minted by the control plane at reap time. On success it
    prints the receipt line the backend parses. Runs over the management
    channel; the user's machine is not involved at any point.
    """
    excludes = " ".join(shlex.quote(arg) for arg in tar_exclude_args())
    sync_mib = max_size_mib(SYNC_MAX_FILE_SIZE)
    artifacts_mib = max_size_mib(ARTIFACTS_MAX_FILE_SIZE)
    keep = ARTIFACTS_TO_KEEP_DIRNAME
    return f"""#!/usr/bin/env bash
# research_plugin expiry parachute (generated; transfer contract v{TRANSFER_CONTRACT_VERSION}).
# Usage: parachute.sh <single-use presigned PUT URL>
# Tars $RP_EXPERIMENT_DIR with the SAME excludes and per-file size caps as
# the rsync sync contract, uploads it, and prints an RP_PARACHUTE receipt.
# Scope is the experiment dir ONLY: $RP_SANDBOX_DATA_DIR (datasets, caches,
# .rp_runs env dumps) never rides the parachute.
set -euo pipefail
URL="${{1:?usage: parachute.sh <single-use presigned PUT URL>}}"
[ -f /opt/rp/env ] && . /opt/rp/env
RP_EXPERIMENT_ID="${{RP_EXPERIMENT_ID:-unknown}}"
RP_WORKDIR="${{RP_WORKDIR:-/workspace/$RP_EXPERIMENT_ID}}"
RP_EXPERIMENT_DIR="${{RP_EXPERIMENT_DIR:-$RP_WORKDIR}}"
cd "$RP_EXPERIMENT_DIR"
SKIP="$(mktemp)"
TAR="$(mktemp)"
trap 'rm -f "$SKIP" "$TAR"' EXIT
# Per-file size caps per the shared contract: {sync_mib} MiB generally,
# {artifacts_mib} MiB inside {keep}/ (mirrors the rsync --max-size pair).
find . -path ./{keep} -prune -o -type f -size +{sync_mib}M -print > "$SKIP"
if [ -d ./{keep} ]; then
  find ./{keep} -type f -size +{artifacts_mib}M -print >> "$SKIP"
fi
tar -czf "$TAR" {excludes} --exclude-from="$SKIP" .
SIZE="$(wc -c < "$TAR" | tr -d ' ')"
SHA="$(sha256sum "$TAR" | cut -d' ' -f1)"
curl -fsS -T "$TAR" "$URL" >/dev/null
printf '{PARACHUTE_RECEIPT_PREFIX.strip()} sha256=%s size=%s\\n' "$SHA" "$SIZE"
"""


def parse_parachute_receipt(output: str) -> dict[str, object] | None:
    """Extract ``{sha256, size_bytes}`` from parachute output, or None."""
    for raw in reversed((output or "").splitlines()):
        line = raw.strip()
        if not line.startswith(PARACHUTE_RECEIPT_PREFIX):
            continue
        fields = dict(
            part.split("=", 1)
            for part in line[len(PARACHUTE_RECEIPT_PREFIX):].split()
            if "=" in part
        )
        sha256 = str(fields.get("sha256") or "")
        try:
            size_bytes = int(str(fields.get("size") or ""))
        except ValueError:
            return None
        if len(sha256) != 64:
            return None
        return {"sha256": sha256, "size_bytes": size_bytes}
    return None

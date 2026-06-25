"""Provider-neutral sandbox sync directory and version contract."""

from __future__ import annotations

import posixpath

from .paths import safe_experiment_dirname


DEFAULT_REMOTE_ROOT = "/workspace"
# Conventional VM-local home for datasets, caches, and other heavy files the
# agent does not want in the repo (``$RP_DATASET_DIR``). Never synced.
DEFAULT_DATA_DIR = "/workspace/data"
SESSIONS_DIRNAME = ".research_plugin_sessions"
ARTIFACTS_TO_KEEP_DIRNAME = "artifacts_to_keep"

# Bumping this version invalidates outstanding sync sessions. Bump it whenever
# the shared transfer excludes, caps, or remote directory contract changes.
TRANSFER_CONTRACT_VERSION = 1
# Bumping this version invalidates outstanding sync sessions. Bump it whenever
# the session payload shape changes.
SYNC_SESSION_SCHEMA_VERSION = 2

# Per-subtree authority (plan §3.1 / fixed decision 8). These name what the
# rsync flags implement: the experiment dir is mirrored with the remote as the
# authority while a sandbox lives (pull --delete), and artifacts_to_keep rides
# its own append-only-shaped 5 GB pass.
EXPERIMENT_DIR_POLICY = "remote_authoritative_for_results"
ARTIFACTS_TO_KEEP_POLICY = "remote_append_only"
DIRECTION_POLICY: dict[str, str] = {
    "experiment_dir": EXPERIMENT_DIR_POLICY,
    "artifacts_to_keep": ARTIFACTS_TO_KEEP_POLICY,
}


def remote_experiment_dir(
    *,
    experiment_id: str,
    name: str = "",
    root: str = DEFAULT_REMOTE_ROOT,
    sandbox_uid: str = "",
) -> str:
    """The one synced folder on the VM for this experiment."""
    folder = safe_experiment_dirname(name.strip() or experiment_id)
    if sandbox_uid:
        # Additional sandboxes need separate remote roots; the default path stays stable.
        folder = safe_experiment_dirname(f"{folder}-{sandbox_uid[:12]}")
    return posixpath.join(
        root.rstrip("/") or "/", folder
    )


def remote_sessions_dir(*, experiment_id: str, root: str = DEFAULT_REMOTE_ROOT) -> str:
    """Where the VM writes its own telemetry, outside the experiment folder."""
    return posixpath.join(
        root.rstrip("/") or "/", SESSIONS_DIRNAME, safe_experiment_dirname(experiment_id)
    )


def remote_root_of(experiment_dir: str) -> str:
    """Recover the remote root from a stored per-experiment dir."""
    return posixpath.dirname(experiment_dir.rstrip("/")) or DEFAULT_REMOTE_ROOT


def sync_hint() -> str:
    return (
        "Work inside the experiment folder ($RP_EXPERIMENT_DIR): it is the "
        "only directory that syncs back to the local repo. Keep datasets, "
        "caches, checkpoints, and anything else you do not want carried into "
        "the repo OUTSIDE the folder (e.g. $RP_DATASET_DIR) — nothing outside "
        "it is ever synced. Put deliberate large final artifacts in "
        "$RP_EXPERIMENT_DIR/artifacts_to_keep, which syncs with a 5 GB "
        "per-file cap instead of the usual 100 MB."
    )

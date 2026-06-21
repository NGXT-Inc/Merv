"""Local management key custody adapter.

The service layer consumes the neutral ``MgmtKeyStore`` port; this module is
the local filesystem implementation used by local-mode composition. Keys live
under ``.research_plugin/mgmt_keys/<experiment_id>/`` in local mode. A cloud
control plane can provide a different implementation behind the same port.
"""

from __future__ import annotations

from pathlib import Path

from ..sandbox.sandbox_support import _safe_name
from ..ssh_keys import ensure_ed25519_keypair


class LocalMgmtKeyStore:
    """Management keys on the control plane's local disk."""

    def __init__(self, *, root: Path) -> None:
        self.root = root

    def key_path(self, *, experiment_id: str) -> Path:
        return self.root / _safe_name(experiment_id) / "key"

    def ensure(self, *, experiment_id: str) -> str:
        key_path = self.key_path(experiment_id=experiment_id)
        return ensure_ed25519_keypair(
            key_path=key_path,
            comment=f"research-plugin-mgmt-{experiment_id}",
            missing_action="mint the sandbox management key",
            failure_subject="sandbox management key",
        )

    def remove(self, *, experiment_id: str) -> None:
        key_path = self.key_path(experiment_id=experiment_id)
        for path in (key_path, key_path.with_suffix(".pub")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            key_path.parent.rmdir()
        except OSError:
            pass

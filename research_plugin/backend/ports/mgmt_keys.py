"""Port for per-sandbox management key custody."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class MgmtKeyStore(Protocol):
    """Control-plane custody of the management SSH channel key."""

    def ensure(self, *, experiment_id: str) -> str:
        """Return the public key authorized for the sandbox management channel."""
        ...

    def key_path(self, *, experiment_id: str) -> Path:
        """Return the private-key path used for the management SSH channel."""
        ...

    def remove(self, *, experiment_id: str) -> None:
        """Drop key material when this store owns its lifecycle."""
        ...

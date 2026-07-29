# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Write-only, process-local Sandbox secret custody.

Provider credentials never enter sandbox rows, events, snapshots, or logs.
This small boundary holds a provisioning token only until post-boot delivery,
then forgets it. Terminal cleanup also forgets delivery bookkeeping so a
long-lived control process cannot retain identifiers indefinitely.
"""

from __future__ import annotations

import threading


class EphemeralSecretCustody:
    """Thread-safe custody for secrets that must never be persisted."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._delivered: set[str] = set()
        self._lock = threading.Lock()

    def remember(self, *, sandbox_uid: str, hf_token: str) -> None:
        if not sandbox_uid or not hf_token:
            return
        with self._lock:
            self._tokens[sandbox_uid] = hf_token

    def pending(self, *, sandbox_uid: str) -> bool:
        if not sandbox_uid:
            return False
        with self._lock:
            return sandbox_uid not in self._delivered

    def hf_token(self, *, sandbox_uid: str) -> str:
        with self._lock:
            return self._tokens.get(sandbox_uid, "")

    def mark_delivered(self, *, sandbox_uid: str) -> None:
        if not sandbox_uid:
            return
        with self._lock:
            self._tokens.pop(sandbox_uid, None)
            self._delivered.add(sandbox_uid)

    def forget(self, *, sandbox_uid: str) -> None:
        if not sandbox_uid:
            return
        with self._lock:
            self._tokens.pop(sandbox_uid, None)
            self._delivered.discard(sandbox_uid)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()
            self._delivered.clear()


__all__ = ["EphemeralSecretCustody"]

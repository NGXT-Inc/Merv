# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Durable management-key adapters."""

from __future__ import annotations

from contextlib import suppress
import hashlib
import os
from pathlib import Path
import stat
import subprocess

from ..kernel.utils import ValidationError


def _safe_name(identity: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in identity
    )
    return safe or "sandbox"


def _ensure_ed25519_keypair(*, key_path: Path, comment: str) -> str:
    public_path = key_path.with_suffix(".pub")
    if key_path.exists() and public_path.exists():
        return public_path.read_text(encoding="utf-8").strip()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    for path in (key_path, public_path):
        if path.exists():
            path.unlink()
    try:
        subprocess.run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-N",
                "",
                "-q",
                "-C",
                comment,
                "-f",
                str(key_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValidationError(
            "ssh-keygen is required to mint the sandbox management key"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr or exc.stdout or str(exc)
        raise ValidationError(
            f"failed to generate sandbox management key: {detail}"
        ) from exc
    with suppress(OSError):
        os.chmod(key_path, 0o600)
    return public_path.read_text(encoding="utf-8").strip()


class LocalMgmtKeyStore:
    """Per-sandbox keys generated under the local brain state directory."""

    def __init__(self, *, root: Path) -> None:
        self.root = root

    def key_path(self, *, sandbox_uid: str) -> Path:
        return self.root / _safe_name(sandbox_uid) / "key"

    def ensure(self, *, sandbox_uid: str) -> str:
        return _ensure_ed25519_keypair(
            key_path=self.key_path(sandbox_uid=sandbox_uid),
            comment=f"research-plugin-mgmt-{sandbox_uid}",
        )

    def remove(self, *, sandbox_uid: str) -> None:
        key_path = self.key_path(sandbox_uid=sandbox_uid)
        for path in (key_path, key_path.with_suffix(".pub")):
            with suppress(OSError):
                path.unlink(missing_ok=True)
        with suppress(OSError):
            key_path.parent.rmdir()


class MountedMgmtKeyStore:
    """One externally managed key shared by a control deployment."""

    def __init__(self, *, private_key_path: Path, public_key: str | None = None) -> None:
        self._private_key_path = private_key_path.expanduser()
        self._private_key_digest = self._read_private_key_digest()
        supplied = (
            public_key
            if public_key is not None
            else self._read_adjacent_public_key()
        )
        self._public_key = self._normalize_public_key(supplied)

    def key_path(self, *, sandbox_uid: str) -> Path:
        del sandbox_uid
        self._assert_private_key_unchanged()
        return self._private_key_path

    def ensure(self, *, sandbox_uid: str) -> str:
        del sandbox_uid
        self._assert_private_key_unchanged()
        return self._public_key

    def remove(self, *, sandbox_uid: str) -> None:
        del sandbox_uid

    def _read_adjacent_public_key(self) -> str:
        public_path = Path(f"{self._private_key_path}.pub")
        try:
            return public_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationError(
                "configured management private key needs either "
                "MERV_MGMT_PUBLIC_KEY or an adjacent .pub file",
                details={"public_key_path": str(public_path)},
            ) from exc

    def _read_private_key_digest(self) -> str:
        try:
            key_stat = self._private_key_path.stat()
        except OSError as exc:
            raise ValidationError(
                "configured management private key does not exist",
                details={"path": str(self._private_key_path)},
            ) from exc
        mode = stat.S_IMODE(key_stat.st_mode)
        if mode & 0o077:
            raise ValidationError(
                "configured management private key permissions are too open "
                "(expected 0600 or stricter)",
                details={"path": str(self._private_key_path), "mode": oct(mode)},
            )
        try:
            data = self._private_key_path.read_bytes()
        except OSError as exc:
            raise ValidationError(
                "configured management private key is not readable",
                details={"path": str(self._private_key_path)},
            ) from exc
        if b"PRIVATE KEY" not in data:
            raise ValidationError(
                "configured management private key does not look like a private key",
                details={"path": str(self._private_key_path)},
            )
        return hashlib.sha256(data).hexdigest()

    def _assert_private_key_unchanged(self) -> None:
        if self._read_private_key_digest() != self._private_key_digest:
            raise ValidationError(
                "configured management private key changed after control startup; "
                "drain live sandboxes or restart control before rotating it",
                details={"path": str(self._private_key_path)},
            )

    @staticmethod
    def _normalize_public_key(public_key: str) -> str:
        value = public_key.strip()
        if not value:
            raise ValidationError("configured management public key is empty")
        if not value.startswith(("ssh-", "ecdsa-", "sk-ssh-")):
            raise ValidationError(
                "configured management public key does not look like an OpenSSH key"
            )
        return value


__all__ = [
    "LocalMgmtKeyStore",
    "MountedMgmtKeyStore",
]

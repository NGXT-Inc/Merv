"""Stable stdlib-only tool invariants shared by both planes."""

from __future__ import annotations

from typing import Optional


_PUBLIC_KEY_PREFIXES = (
    "ssh-ed25519 ",
    "ssh-rsa ",
    "ecdsa-sha2-nistp256 ",
    "ecdsa-sha2-nistp384 ",
    "ecdsa-sha2-nistp521 ",
    "sk-ssh-ed25519@openssh.com ",
    "sk-ecdsa-sha2-nistp256@openssh.com ",
)


def validate_openssh_public_key(value: Optional[str]) -> Optional[str]:
    """Normalize and validate a supported single-line OpenSSH public key."""
    if value is None:
        return None
    key = value.strip()
    if not key:
        return None
    lowered = key.lower()
    if "private key" in lowered or key.startswith("-----BEGIN "):
        raise ValueError(
            "public_key must be an OpenSSH public key, not private-key material"
        )
    if "\n" in key or "\r" in key:
        raise ValueError("public_key must be a single line")
    if len(key) < 40 or len(key) > 8192:
        raise ValueError("public_key length is outside the accepted OpenSSH range")
    if not key.startswith(_PUBLIC_KEY_PREFIXES):
        raise ValueError(
            "public_key must start with a supported OpenSSH public key type"
        )
    parts = key.split()
    if len(parts) < 2:
        raise ValueError("public_key must include key type and base64 payload")
    return key


def validate_resource_register_mode(
    *,
    path: object = None,
    paths: object = None,
    resource_id: object = None,
    target_type: object = None,
    target_id: object = None,
    role: object = None,
) -> None:
    """Validate the mutually exclusive resource.register operating modes."""
    sources = [path is not None, paths is not None, resource_id is not None]
    if sum(sources) != 1:
        raise ValueError("provide exactly one of 'path', 'paths', or 'resource_id'")
    trio_present = [
        target_type is not None,
        target_id is not None,
        role is not None,
    ]
    if any(trio_present) and not all(trio_present):
        raise ValueError("target_type, target_id, and role must be provided together")
    if resource_id is not None and not all(trio_present):
        raise ValueError(
            "resource_id association mode requires target_type, target_id, and role"
        )

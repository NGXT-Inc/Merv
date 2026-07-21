"""Ports required by the Feed application service."""

from __future__ import annotations

from typing import Any, Protocol


class LinkUnfurlError(Exception):
    """A link could not be safely converted into a static preview."""


class LinkUnfurlPort(Protocol):
    """Bounded network access used when a feed post contains a link."""

    def unfurl(self, url: str) -> dict[str, Any]: ...

    def fetch_preview_image(self, image_url: str) -> tuple[bytes, str]: ...


__all__ = ["LinkUnfurlError", "LinkUnfurlPort"]

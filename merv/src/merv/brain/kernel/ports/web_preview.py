"""Ports for bounded outbound web previews.

The contracts live in Kernel because Feed and Research both consume preview
metadata while the network implementation belongs to an outer adapter.
"""

from __future__ import annotations

from typing import Any, Protocol


class WebPreviewError(Exception):
    """A URL could not be converted into a safe, static preview."""


class WebPreview(Protocol):
    """Feed-facing link preview capability."""

    def unfurl(self, url: str) -> dict[str, Any]: ...

    def fetch_preview_image(self, image_url: str) -> tuple[bytes, str]: ...


class PaperPreview(Protocol):
    """Literature-facing paper metadata capability."""

    def allowed(self, url: str) -> bool: ...

    def unfurl(self, url: str) -> dict[str, Any]: ...


__all__ = ["PaperPreview", "WebPreview", "WebPreviewError"]

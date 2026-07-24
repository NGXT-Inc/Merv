"""Control-side HTTP endpoints used by stateless local data-plane proxies."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import Body, Request
from merv.shared.feed_embeds import MAX_FEED_EMBED_BYTES
from merv.shared.feed_images import MAX_FEED_IMAGE_BYTES

from ...feed.facade import FeedDelivery
from ...kernel.utils import ValidationError
from .api.dependencies import AuthorizeProject

JsonBody = dict[str, Any] | None

def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None or str(value) == "":
        raise ValidationError(f"{key} is required")
    return str(value)


def _decode_b64_field(
    value: Any, *, label: str, max_decoded_bytes: int | None = None
) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{label} must be non-empty base64")
    if max_decoded_bytes is not None:
        max_encoded_chars = ((max_decoded_bytes + 2) // 3) * 4
        if len(value) > max_encoded_chars:
            raise ValidationError(
                f"{label} decodes above the {max_decoded_bytes} byte limit"
            )
    try:
        data = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValidationError(f"{label} must be valid base64") from exc
    if max_decoded_bytes is not None and len(data) > max_decoded_bytes:
        raise ValidationError(
            f"{label} decodes to {len(data)} bytes; limit is {max_decoded_bytes}"
        )
    return data


def register_data_plane_routes(
    http: Any,
    *,
    authorize_project: AuthorizeProject,
    feed: FeedDelivery,
) -> None:
    @http.post("/api/data-plane/feed/validate-post")
    def data_plane_validate_feed_post(
        request: Request, body: JsonBody = Body(default=None)
    ) -> dict[str, Any]:
        payload = body or {}
        project_id = _required_text(payload, "project_id")
        authorize_project(request, project_id)
        return feed.validate_post_intent(
            project_id=project_id,
            handle=_required_text(payload, "handle"),
            text=_required_text(payload, "text"),
            ref=payload.get("ref"),
            kind=payload.get("kind"),
            in_reply_to=payload.get("in_reply_to"),
        )

    @http.post("/api/data-plane/feed/post")
    def data_plane_post_feed(
        request: Request, body: JsonBody = Body(default=None)
    ) -> dict[str, Any]:
        payload = body or {}
        project_id = _required_text(payload, "project_id")
        authorize_project(request, project_id)
        feed.validate_post_intent(
            project_id=project_id,
            handle=_required_text(payload, "handle"),
            text=_required_text(payload, "text"),
            ref=payload.get("ref"),
            kind=payload.get("kind"),
        )
        image = payload.get("image")
        image_bytes = None
        image_path = None
        if image is not None:
            if not isinstance(image, dict):
                raise ValidationError("image must be an object")
            image_path = str(image.get("path") or "feed-image")
            image_bytes = _decode_b64_field(
                image.get("data_b64"),
                label="image.data_b64",
                max_decoded_bytes=MAX_FEED_IMAGE_BYTES,
            )
        html = payload.get("html")
        html_bytes = None
        html_path = None
        if html is not None:
            if not isinstance(html, dict):
                raise ValidationError("html must be an object")
            html_path = str(html.get("path") or "feed-embed")
            html_bytes = _decode_b64_field(
                html.get("data_b64"),
                label="html.data_b64",
                max_decoded_bytes=MAX_FEED_EMBED_BYTES,
            )
        return feed.post_observed(
            project_id=project_id,
            handle=_required_text(payload, "handle"),
            text=_required_text(payload, "text"),
            image_path=image_path,
            image_bytes=image_bytes,
            html_path=html_path,
            html_bytes=html_bytes,
            url=payload.get("url"),
            ref=payload.get("ref"),
            kind=payload.get("kind"),
            in_reply_to=payload.get("in_reply_to"),
        )

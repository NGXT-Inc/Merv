"""The review-pinning snapshot id: a byte-stable equality key.

Compared for equality in services/reviews.py and parsed back by
snapshot_from_id, so this format is load-bearing and must not drift.
"""
from __future__ import annotations
from typing import Any

def review_snapshot_id(*, target_type: str, target: dict[str, Any]) -> str:
    """`type|id|status|attempt|sorted-comma-joined-resource-tokens`.

    `target` is a get_state() dict with id/status/attempt_index and
    current_attempt_resources. Field order and token format are an
    equality key — keep byte-identical."""
    resource_tokens = [
        f"{res['id']}:{res.get('association_version_id') or res['version_token']}:{res.get('association_role', '')}:{res.get('association_attempt_index', 0)}"
        for res in target.get("current_attempt_resources", [])
    ]
    return "|".join(
        [
            target_type,
            target["id"],
            target["status"],
            str(target["attempt_index"]),
            ",".join(sorted(resource_tokens)),
        ]
    )

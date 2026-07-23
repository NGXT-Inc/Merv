"""Application contracts for project-scoped heavy-object storage."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict, runtime_checkable


class ProducedObject(TypedDict):
    """Hosted-safe ledger fields used in experiment response composition."""

    id: str
    name: str
    version: int
    kind: str
    content_sha256: str
    size_bytes: int
    content_type: str
    status: str
    expires_at: str | None
    producing_run: str
    source_uri: str
    notes: str
    created_at: str
    updated_at: str
    last_accessed_at: str | None


@runtime_checkable
class ProducedObjectCatalog(Protocol):
    """Batch heavy-object facts for experiment response presentation."""

    def by_experiment(
        self, *, project_id: str, experiment_ids: tuple[str, ...]
    ) -> dict[str, list[ProducedObject]]: ...


@runtime_checkable
class ObjectStorage(Protocol):
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def complete_upload(self, **kwargs: Any) -> dict[str, Any]: ...
    def submit(self, **kwargs: Any) -> dict[str, Any]: ...
    def fetch(self, **kwargs: Any) -> dict[str, Any]: ...
    def complete_via_token(self, **kwargs: Any) -> dict[str, Any]: ...
    def list_objects(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def resolve(self, **kwargs: Any) -> dict[str, Any]: ...
    def pin(self, **kwargs: Any) -> dict[str, Any]: ...
    def unpin(self, **kwargs: Any) -> dict[str, Any]: ...
    def renew(self, **kwargs: Any) -> dict[str, Any]: ...
    def delete(self, **kwargs: Any) -> dict[str, Any]: ...


__all__ = ["ObjectStorage", "ProducedObject", "ProducedObjectCatalog"]

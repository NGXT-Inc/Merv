"""Local heavy-object store using presigned file:// upload/download stand-ins."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..ports.object_store import ObjectStat
from ..state.blobs import _validate_keys
from ..utils import NotFoundError, ValidationError, new_id, now_iso


class LocalObjectStore:
    """Object store rooted at a local directory (local-mode implementation).

    Layout: ``<root>/<namespace>/<sha[:2]>/<sha>`` with a ``<sha>.meta.json``
    sidecar. Upload targets live under ``<root>/.uploads/<upload_id>``.
    """

    def __init__(self, *, root: Path) -> None:
        self.root = root

    def presign_upload(
        self,
        *,
        namespace: str,
        sha256: str,
        size_bytes: int,
        content_type: str = "application/octet-stream",
        expires_in: int,
    ) -> dict[str, Any]:
        """Mint a local staging file target for an off-process producer.

        The ``file://`` URL is honest to the seam, not the transport: cloud
        providers return reachable HTTPS URLs behind these same verbs.
        """
        _validate_keys(namespace=namespace, sha256=sha256)
        upload_id = new_id(prefix="upload")
        staging = self._staging_path(upload_id=upload_id)
        staging.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "upload_id": upload_id,
            "namespace": namespace,
            "sha256": sha256,
            "size_bytes": int(size_bytes),
            "content_type": content_type,
            "created_at": now_iso(),
        }
        self._staging_meta_path(upload_id=upload_id).write_text(
            json.dumps(meta, sort_keys=True), encoding="utf-8"
        )
        return {
            "upload_id": upload_id,
            "url": staging.resolve().as_uri(),
            "size_bytes": int(size_bytes),
            "content_type": content_type,
            "expires_in": int(expires_in),
        }

    def complete_upload(
        self, *, upload_id: str, parts: list[dict[str, Any]] | None = None
    ) -> ObjectStat:
        staging = self._staging_path(upload_id=upload_id)
        meta_path = self._staging_meta_path(upload_id=upload_id)
        if not meta_path.exists():
            raise NotFoundError(f"unknown or already-consumed upload: {upload_id}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        try:
            if not staging.exists():
                raise NotFoundError(f"upload received no bytes: {upload_id}")
            sha, size = self._hash_and_size(staging)
            size_bytes = int(meta["size_bytes"])
            if size > size_bytes:
                raise ValidationError(
                    f"upload {upload_id} exceeds its size cap: "
                    f"{size} > {size_bytes} bytes"
                )
            if sha != str(meta["sha256"]):
                raise ValidationError(
                    f"upload {upload_id} checksum mismatch: "
                    f"expected {meta['sha256']}, got {sha}"
                )
            blob_path = self._object_path(namespace=str(meta["namespace"]), sha256=sha)
            if not blob_path.exists():
                blob_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, blob_path)
                self._write_meta(
                    namespace=str(meta["namespace"]),
                    sha256=sha,
                    size_bytes=size,
                    content_type=str(meta["content_type"]),
                    created_at=now_iso(),
                    expires_at=None,
                )
            stat = self.stat(namespace=str(meta["namespace"]), sha256=sha)
            assert stat is not None
            return stat
        finally:
            for path in (staging, meta_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    def presign_download(
        self, *, namespace: str, sha256: str, expires_in: int
    ) -> dict[str, Any]:
        _validate_keys(namespace=namespace, sha256=sha256)
        path = self._object_path(namespace=namespace, sha256=sha256)
        if not path.exists():
            raise NotFoundError(f"object not found: {namespace}/{sha256}")
        return {"url": path.resolve().as_uri(), "expires_in": int(expires_in)}

    def stat(self, *, namespace: str, sha256: str) -> ObjectStat | None:
        _validate_keys(namespace=namespace, sha256=sha256)
        path = self._object_path(namespace=namespace, sha256=sha256)
        meta_path = self._meta_path(namespace=namespace, sha256=sha256)
        if not path.exists() or not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return ObjectStat(
            sha256=str(meta["sha256"]),
            namespace=str(meta["namespace"]),
            size_bytes=int(meta["size_bytes"]),
            content_type=str(meta["content_type"]),
            created_at=str(meta["created_at"]),
            expires_at=meta.get("expires_at"),
        )

    def delete(self, *, namespace: str, sha256: str) -> bool:
        _validate_keys(namespace=namespace, sha256=sha256)
        path = self._object_path(namespace=namespace, sha256=sha256)
        meta_path = self._meta_path(namespace=namespace, sha256=sha256)
        existed = path.exists()
        for target in (path, meta_path):
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        return existed

    def set_expiry(
        self, *, namespace: str, sha256: str, expires_at: str | None
    ) -> None:
        _validate_keys(namespace=namespace, sha256=sha256)
        meta_path = self._meta_path(namespace=namespace, sha256=sha256)
        if not self._object_path(namespace=namespace, sha256=sha256).exists():
            raise NotFoundError(f"object not found: {namespace}/{sha256}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        current = meta.get("expires_at")
        if current is None and meta.get("pinned"):
            return
        if expires_at is None:
            meta["expires_at"] = None
            meta["pinned"] = True
            meta_path.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
        elif current is None or str(expires_at) > str(current):
            meta["expires_at"] = expires_at
            meta.pop("pinned", None)
            meta_path.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")

    def sweep_expired(self, *, now: str | None = None) -> int:
        cutoff = now or now_iso()
        swept = 0
        if not self.root.exists():
            return 0
        for meta_path in self.root.glob("*/*/*.meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            expires_at = meta.get("expires_at")
            if not expires_at or str(expires_at) > cutoff:
                continue
            if self.delete(namespace=str(meta["namespace"]), sha256=str(meta["sha256"])):
                swept += 1
        return swept

    def _object_path(self, *, namespace: str, sha256: str) -> Path:
        return self.root / namespace / sha256[:2] / sha256

    def _meta_path(self, *, namespace: str, sha256: str) -> Path:
        return self.root / namespace / sha256[:2] / f"{sha256}.meta.json"

    def _staging_path(self, *, upload_id: str) -> Path:
        return self.root / ".uploads" / upload_id

    def _staging_meta_path(self, *, upload_id: str) -> Path:
        return self.root / ".uploads" / f"{upload_id}.meta.json"

    def _hash_and_size(self, path: Path) -> tuple[str, int]:
        # Streamed to avoid loading GB-scale staged files into memory.
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def _write_meta(
        self,
        *,
        namespace: str,
        sha256: str,
        size_bytes: int,
        content_type: str,
        created_at: str,
        expires_at: str | None,
    ) -> None:
        meta = {
            "sha256": sha256,
            "namespace": namespace,
            "size_bytes": int(size_bytes),
            "content_type": content_type,
            "created_at": created_at,
            "expires_at": expires_at,
        }
        self._meta_path(namespace=namespace, sha256=sha256).write_text(
            json.dumps(meta, sort_keys=True), encoding="utf-8"
        )

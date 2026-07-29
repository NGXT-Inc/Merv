"""Binary blob contract shared by local, fake, and S3 adapters."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from merv.brain.kernel.ports.blob_store import validate_blob_keys
from merv.brain.object_storage.blobs import LocalDirBlobStore
from merv.brain.kernel.utils import NotFoundError, ValidationError
from tests.fakes import FakeBlobStore


class BlobStoreContractMixin:
    """One behavioral suite run against every BlobStore implementation."""

    def make_store(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def test_put_get_round_trip_and_content_addressing(self) -> None:
        store = self.make_store()
        data = b"hello blobs"
        sha = store.put(namespace="proj_a", data=data)
        self.assertEqual(sha, hashlib.sha256(data).hexdigest())
        self.assertEqual(store.get(namespace="proj_a", sha256=sha), data)
        # Idempotent re-put returns the same key.
        self.assertEqual(store.put(namespace="proj_a", data=data), sha)

    def test_namespace_isolation(self) -> None:
        store = self.make_store()
        sha = store.put(namespace="proj_a", data=b"scoped")
        with self.assertRaises(NotFoundError):
            store.get(namespace="proj_b", sha256=sha)

    def test_ttl_sweep_with_injected_clock(self) -> None:
        store = self.make_store()
        expiring = store.put(
            namespace="proj_a", data=b"temporary", expires_at="2026-01-01T00:00:00Z"
        )
        keeper = store.put(namespace="proj_a", data=b"permanent")
        later = store.put(
            namespace="proj_a", data=b"later", expires_at="2027-01-01T00:00:00Z"
        )
        swept = store.sweep_expired(now="2026-06-01T00:00:00Z")
        self.assertEqual(swept, 1)
        with self.assertRaises(NotFoundError):
            store.get(namespace="proj_a", sha256=expiring)
        self.assertEqual(store.get(namespace="proj_a", sha256=keeper), b"permanent")
        self.assertEqual(store.get(namespace="proj_a", sha256=later), b"later")

    def test_reput_only_extends_expiry(self) -> None:
        store = self.make_store()
        sha = store.put(
            namespace="proj_a", data=b"pin me", expires_at="2026-01-01T00:00:00Z"
        )
        # Re-put with no expiry pins the blob forever.
        store.put(namespace="proj_a", data=b"pin me")
        self.assertEqual(store.sweep_expired(now="2026-06-01T00:00:00Z"), 0)
        self.assertEqual(store.get(namespace="proj_a", sha256=sha), b"pin me")

class LocalDirBlobStoreTest(BlobStoreContractMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_store(self) -> LocalDirBlobStore:
        return LocalDirBlobStore(root=Path(self.tmp.name) / "blobs")


class FakeBlobStoreTest(BlobStoreContractMixin, unittest.TestCase):
    def make_store(self) -> FakeBlobStore:
        return FakeBlobStore()


class BlobKeyValidationTest(unittest.TestCase):
    def test_rejects_unsafe_namespaces_and_non_hex_digests(self) -> None:
        validate_blob_keys(namespace="proj_valid-1", sha256="a" * 64)
        for namespace in ("", "not/a/namespace", "white space"):
            with self.subTest(namespace=namespace), self.assertRaises(ValidationError):
                validate_blob_keys(namespace=namespace)
        with self.assertRaises(ValidationError):
            validate_blob_keys(namespace="proj_valid", sha256="ABC")


if __name__ == "__main__":
    unittest.main()

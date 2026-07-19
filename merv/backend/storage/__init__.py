"""Transitional shim — moved to backend.object_storage; deleted at de-shim."""

import sys

from .. import object_storage as _moved
from ..object_storage import blobs, file_transfer, s3_object_store, storage_guidance

# Identity-preserving alias: old ``backend.storage[.X]`` names resolve to the
# canonical ``backend.object_storage`` module objects. ``service``/``s3_blobs``
# stay lazy (no eager state/boto pull); they load through the aliased package's
# __path__ and their relative imports hit the aliases seeded below.
sys.modules[__name__] = _moved
for _submodule in (blobs, file_transfer, s3_object_store, storage_guidance):
    sys.modules[f"{__name__}.{_submodule.__name__.rsplit('.', 1)[-1]}"] = _submodule

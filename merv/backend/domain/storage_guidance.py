"""Transitional shim — moved to backend.object_storage.storage_guidance; deleted at de-shim."""

import sys

from ..object_storage import storage_guidance as _moved

sys.modules[__name__] = _moved

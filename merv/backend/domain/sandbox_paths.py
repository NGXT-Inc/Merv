"""Transitional shim — moved to backend.sandbox.sandbox_paths; deleted at de-shim."""

import sys

from ..sandbox import sandbox_paths as _moved

sys.modules[__name__] = _moved

"""Transitional shim — moved to backend.sandbox.transcript_cache; deleted at de-shim."""

import sys

from ..sandbox import transcript_cache as _moved

sys.modules[__name__] = _moved

"""Transitional shim — moved to backend.sandbox.quotas; deleted at de-shim."""

import sys

from ..sandbox import quotas as _moved

sys.modules[__name__] = _moved

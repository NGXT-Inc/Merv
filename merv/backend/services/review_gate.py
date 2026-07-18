"""Transitional shim — moved to backend.research_core.review_gate; deleted at de-shim."""
import sys
from ..research_core import review_gate as _moved
sys.modules[__name__] = _moved

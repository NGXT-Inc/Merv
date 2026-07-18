"""Transitional shim — moved to backend.research_core.domain.review_snapshot; deleted at de-shim."""
import sys
from ..research_core.domain import review_snapshot as _moved
sys.modules[__name__] = _moved

"""Transitional shim — moved to backend.research_core.domain.artifacts; deleted at de-shim."""
import sys
from ..research_core.domain import artifacts as _moved
sys.modules[__name__] = _moved

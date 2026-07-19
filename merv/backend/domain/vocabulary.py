"""Transitional shim — moved to backend.research_core.domain.vocabulary; deleted at de-shim."""
import sys
from ..research_core.domain import vocabulary as _moved
sys.modules[__name__] = _moved

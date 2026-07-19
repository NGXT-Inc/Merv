"""Transitional shim — moved to backend.research_core.domain.synopsis; deleted at de-shim."""
import sys
from ..research_core.domain import synopsis as _moved
sys.modules[__name__] = _moved

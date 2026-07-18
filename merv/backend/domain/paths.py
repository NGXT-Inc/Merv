"""Transitional shim — moved to backend.research_core.domain.paths; deleted at de-shim."""
import sys
from ..research_core.domain import paths as _moved
sys.modules[__name__] = _moved

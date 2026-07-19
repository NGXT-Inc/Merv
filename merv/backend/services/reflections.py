"""Transitional shim — moved to backend.research_core.reflections; deleted at de-shim."""
import sys
from ..research_core import reflections as _moved
sys.modules[__name__] = _moved

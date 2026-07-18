"""Transitional shim — moved to backend.research_core.reflection_tools; deleted at de-shim."""
import sys
from ..research_core import reflection_tools as _moved
sys.modules[__name__] = _moved

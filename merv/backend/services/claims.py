"""Transitional shim — moved to backend.research_core.claims; deleted at de-shim."""
import sys
from ..research_core import claims as _moved
sys.modules[__name__] = _moved

"""Transitional shim — moved to backend.research_core.association_targets; deleted at de-shim."""
import sys
from ..research_core import association_targets as _moved
sys.modules[__name__] = _moved

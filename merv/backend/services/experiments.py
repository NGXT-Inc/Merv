"""Transitional shim — moved to backend.research_core.experiments; deleted at de-shim."""
import sys
from ..research_core import experiments as _moved
sys.modules[__name__] = _moved

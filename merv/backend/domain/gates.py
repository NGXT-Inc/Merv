"""Transitional shim — moved to backend.research_core.domain.gates; deleted at de-shim."""
import sys
from ..research_core.domain import gates as _moved
sys.modules[__name__] = _moved

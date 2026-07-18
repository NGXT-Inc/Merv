"""Transitional shim — moved to backend.research_core.domain.reflection_gates; deleted at de-shim."""
import sys
from ..research_core.domain import reflection_gates as _moved
sys.modules[__name__] = _moved

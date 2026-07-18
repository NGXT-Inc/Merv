"""Transitional shim — moved to backend.research_core.domain.reflection_policy; deleted at de-shim."""
import sys
from ..research_core.domain import reflection_policy as _moved
sys.modules[__name__] = _moved

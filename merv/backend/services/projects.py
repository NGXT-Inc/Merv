"""Transitional shim — moved to backend.research_core.projects; deleted at de-shim."""
import sys
from ..research_core import projects as _moved
sys.modules[__name__] = _moved

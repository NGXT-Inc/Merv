"""Transitional shim — moved to backend.research_core.graph_refs; deleted at de-shim."""
import sys
from ..research_core import graph_refs as _moved
sys.modules[__name__] = _moved

"""Transitional shim — moved to backend.research_core.domain.graph_lint; deleted at de-shim."""
import sys
from ..research_core.domain import graph_lint as _moved
sys.modules[__name__] = _moved

"""Transitional shim — moved to backend.research_core.project_overview; deleted at de-shim."""
import sys
from ..research_core import project_overview as _moved
sys.modules[__name__] = _moved

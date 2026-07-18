"""Transitional shim — moved to backend.research_core.workflow_views; deleted at de-shim."""
import sys
from ..research_core import workflow_views as _moved
sys.modules[__name__] = _moved

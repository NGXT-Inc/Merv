"""Transitional shim — moved to backend.research_core.workflow; deleted at de-shim."""
import sys
from ..research_core import workflow as _moved
sys.modules[__name__] = _moved

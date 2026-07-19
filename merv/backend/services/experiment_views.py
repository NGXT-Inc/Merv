"""Transitional shim — moved to backend.research_core.experiment_views; deleted at de-shim."""
import sys
from ..research_core import experiment_views as _moved
sys.modules[__name__] = _moved

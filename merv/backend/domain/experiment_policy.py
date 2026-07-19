"""Transitional shim — moved to backend.research_core.domain.experiment_policy; deleted at de-shim."""
import sys
from ..research_core.domain import experiment_policy as _moved
sys.modules[__name__] = _moved

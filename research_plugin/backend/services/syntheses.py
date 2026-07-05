"""Compatibility alias for the renamed reflection service.

Phase-6 deletion debt: import backend.services.reflections instead.
"""

from .reflections import ReflectionService, SynthesisService

__all__ = ["ReflectionService", "SynthesisService"]

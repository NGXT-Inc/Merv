"""Artifacts module: resource records, versions, evidence, and projections.

Owns repo-file resource identity and associations (``resources``), the
pinned-bytes rule, and public evidence contracts (``ports``). Shared role
vocabulary and markdown parsing live below both planes in ``merv.shared``.
"""

from __future__ import annotations

from .figure_view import build_experiment_figure
from .resources import ResourceService

__all__ = [
    "ResourceService",
    "build_experiment_figure",
]

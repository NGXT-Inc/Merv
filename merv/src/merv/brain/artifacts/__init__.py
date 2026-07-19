"""Artifacts module: resource records, versions, pinning, roles, projections.

Owns repo-file resource identity and associations (``resources``), the
pinned-bytes rule and its research_core-facing facade (``pinned``), and pure
presentation helpers (``figure_view``, ``resource_selection``). Shared role
vocabulary and markdown parsing live below both planes in ``merv.shared``.
Persists bytes through object_storage — the only module edge it uses.
"""

from __future__ import annotations

from .figure_view import build_experiment_figure
from .pinned import PinnedStore
from .resource_selection import preferred_associated_resource
from .resources import ResourceService

__all__ = [
    "PinnedStore",
    "ResourceService",
    "build_experiment_figure",
    "preferred_associated_resource",
]

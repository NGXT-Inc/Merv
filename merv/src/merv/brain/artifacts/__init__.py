"""Artifacts module: typed submitted artifacts and evidence contracts.

Owns artifact submission (``submissions``), association legality
(``association_policy``), and public evidence contracts (``ports``). Shared
role vocabulary and markdown parsing live below both planes in ``merv.shared``.
"""

from __future__ import annotations

from .submissions import ArtifactSubmissionService

__all__ = ["ArtifactSubmissionService"]

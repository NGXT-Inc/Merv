# If you update this file, you must consult artifacts.md to see whether artifacts.md needs to be updated. artifacts.md must not exceed 100 lines.
"""Artifacts module."""

from __future__ import annotations

from .artifacts import Artifacts, MAX_SUBMITTED_TEXT_BYTES
from .models import (
    Artifact,
    ArtifactTarget,
    CompletedArtifact,
    CompletedFigure,
    PendingUpload,
    Submission,
)

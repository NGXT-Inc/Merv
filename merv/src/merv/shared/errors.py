"""Dependency-free error types shared by the brain and stdio proxy."""

from __future__ import annotations


class ResearchPluginError(Exception):
    """Base class for domain and tool errors."""

    error_code = "research_plugin_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(ResearchPluginError):
    error_code = "not_found"


class PermissionDeniedError(ResearchPluginError):
    error_code = "permission_denied"


class ValidationError(ResearchPluginError):
    error_code = "validation_error"


class WorkflowError(ResearchPluginError):
    error_code = "workflow_error"


class ContentUnavailableError(ResearchPluginError):
    """A file's bytes are not reachable from the current plane."""

    error_code = "content_unavailable"


class DataPlaneRequiredError(ResearchPluginError):
    """The requested mutation must be performed by the local data plane."""

    error_code = "data_plane_required"

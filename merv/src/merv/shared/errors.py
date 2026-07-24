"""Dependency-free error types shared across Merv layers."""

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
    """A file's bytes are not available from the current deployment."""

    error_code = "content_unavailable"

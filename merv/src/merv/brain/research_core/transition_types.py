"""Research-owned values exposed when a workflow transition commits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from ..kernel.events import StoredEvent


class PersistedRunState(TypedDict, total=False):
    run_id: str | None
    run_name: str
    status: str
    artifact_uri: str
    created_at: str | None
    created_by_plugin: bool
    error: str


class ExperimentState(TypedDict, total=False):
    id: str
    project_id: str
    name: str
    status: str
    attempt_index: int
    mlflow_run: PersistedRunState | None


class ExhibitVerdict(TypedDict, total=False):
    runs_found: int
    result_files: int
    attempt_index: int
    mlflow: dict[str, object]
    pinned: bool


class SlimExperimentState(ExperimentState, total=False):
    pass


@dataclass(frozen=True, slots=True)
class CommittedExperimentTransition:
    state: ExperimentState
    event: StoredEvent


__all__ = [
    "CommittedExperimentTransition",
    "ExhibitVerdict",
    "ExperimentState",
    "PersistedRunState",
    "SlimExperimentState",
]

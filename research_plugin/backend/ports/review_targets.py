"""Ports used by the review service."""

from __future__ import annotations

from typing import Any, Protocol


class ReviewPolicy(Protocol):
    """Validates review vocabulary accepted by review requests/submissions."""

    def validate_review_role(self, *, role: str) -> None:
        ...

    def validate_review_verdict(self, *, verdict: str) -> None:
        ...


class ExperimentReviewTarget(Protocol):
    """Experiment operations the review service needs at gate boundaries."""

    def get_state(
        self,
        *,
        experiment_id: str,
        project_id: str | None = None,
        conn: Any | None = None,
    ) -> dict[str, Any]:
        ...

    def target_snapshot_id(self, *, conn: Any, experiment_id: str) -> str:
        ...

    def send_back_to_running(
        self, *, conn: Any, experiment_id: str, revision_context: str
    ) -> None:
        ...

    def send_back_to_planned(
        self, *, conn: Any, experiment_id: str, revision_context: str
    ) -> None:
        ...


class SynthesisReviewTarget(Protocol):
    """Reflection-wave operations the review service needs at gate boundaries."""

    def get_state(
        self,
        *,
        synthesis_id: str,
        project_id: str | None = None,
        conn: Any | None = None,
    ) -> dict[str, Any]:
        ...

    def target_snapshot_id(self, *, conn: Any, synthesis_id: str) -> str:
        ...

    def send_back_to_reflecting(
        self, *, conn: Any, synthesis_id: str, revision_context: str
    ) -> None:
        ...

    def send_back_to_synthesizing(
        self, *, conn: Any, synthesis_id: str, revision_context: str
    ) -> None:
        ...

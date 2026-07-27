"""Canonical agent context for work already inside one experiment."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

from ...artifacts.facade import Artifacts
from ...research_core.facade import (
    EXPERIMENT_TERMINAL_STATUSES,
    ExperimentState,
    preferred_artifact,
)


Record = dict[str, Any]

_PLAN_APPROVED_STATUSES = frozenset(
    {"ready_to_run", "running", "experiment_review", "complete"}
)


class ExperimentContextQuery:
    """Build the one experiment context shape used to initialize agents.

    Normal workflow reads use the current submitted artifact composition.
    Review sessions may provide the immutable artifacts pinned by the review
    request; the resulting shape is identical, but its document bytes and
    artifact rows come from that snapshot.
    """

    def __init__(self, *, artifacts: Artifacts) -> None:
        self.artifacts = artifacts

    def build(
        self,
        *,
        state: ExperimentState | Record,
        project_id: str | None = None,
        pinned_artifacts: Iterable[Mapping[str, Any]] | None = None,
    ) -> Record:
        status = str(state.get("status") or "")
        rows, pinned_content = self._artifact_rows(
            state=state, pinned_artifacts=pinned_artifacts
        )
        plan = preferred_artifact(artifacts=rows, roles=("plan",))
        report = preferred_artifact(artifacts=rows, roles=("report",))
        experiment: Record = {
            "id": state.get("id"),
            "project_id": state.get("project_id") or project_id,
            "name": state.get("name"),
            "status": status,
            "intent": state.get("intent"),
            "tested_claims": [
                {
                    "id": claim.get("id"),
                    "statement": claim.get("statement"),
                }
                for claim in state.get("tested_claims", [])
                if isinstance(claim, dict)
            ],
        }
        if status == "complete":
            experiment["conclusion"] = state.get("conclusion") or ""

        return {
            "experiment": experiment,
            "plan": self._plan(
                artifact=plan,
                state=state,
                status=status,
                pinned_content=pinned_content,
            ),
            "report": self._report(
                artifact=report,
                state=state,
                status=status,
                pinned_content=pinned_content,
            ),
            "artifacts": [
                self._artifact_reference(artifact)
                for artifact in rows
                if str(artifact.get("role") or "") not in {"plan", "report"}
            ],
        }

    def _artifact_rows(
        self,
        *,
        state: ExperimentState | Record,
        pinned_artifacts: Iterable[Mapping[str, Any]] | None,
    ) -> tuple[list[Record], dict[str, str | None]]:
        current = [
            dict(artifact)
            for artifact in state.get("current_attempt_artifacts", [])
            if isinstance(artifact, dict)
        ]
        if pinned_artifacts is None:
            return current, {}

        pinned = [dict(artifact) for artifact in pinned_artifacts]
        by_id = {
            str(artifact.get("id") or ""): artifact
            for artifact in current
            if artifact.get("id")
        }
        rows: list[Record] = []
        content: dict[str, str | None] = {}
        for artifact in pinned:
            artifact_id = str(
                artifact.get("artifact_id") or artifact.get("id") or ""
            )
            if not artifact_id:
                continue
            merged = {
                **by_id.get(artifact_id, {}),
                "id": artifact_id,
                "role": artifact.get("role")
                or by_id.get(artifact_id, {}).get("role"),
                "lens_id": artifact.get("lens_id")
                or by_id.get(artifact_id, {}).get("lens_id")
                or "",
                "path": artifact.get("path")
                or by_id.get(artifact_id, {}).get("path"),
                "attempt_index": (
                    artifact.get("attempt_index")
                    or by_id.get(artifact_id, {}).get("attempt_index")
                    or state.get("attempt_index")
                ),
                "updated_at": (
                    artifact.get("submitted_at")
                    or artifact.get("updated_at")
                    or by_id.get(artifact_id, {}).get("updated_at")
                    or by_id.get(artifact_id, {}).get("created_at")
                    or ""
                ),
            }
            rows.append(merged)
            content[artifact_id] = artifact.get("content")

        # Keep the snapshot order supplied by Review, which is the submitted
        # artifact order. Filtering exclusively through that list also ensures
        # a post-start resubmission cannot leak into this review context.
        return rows, content

    def _plan(
        self,
        *,
        artifact: Record | None,
        state: ExperimentState | Record,
        status: str,
        pinned_content: Mapping[str, str | None],
    ) -> Record:
        if artifact is None:
            return {"status": "missing"}
        result = {
            **self._document_identity(artifact),
            "attempt_index": artifact.get("attempt_index")
            or state.get("attempt_index"),
            "status": self._plan_status(
                state=state, artifact_id=str(artifact.get("id") or "")
            ),
        }
        content = self._content(artifact=artifact, pinned_content=pinned_content)
        if status in EXPERIMENT_TERMINAL_STATUSES:
            result["summary"] = (
                str(artifact.get("tldr") or "").strip()
                or _document_summary(content)
            )
        else:
            result["content"] = content or ""
        return result

    def _report(
        self,
        *,
        artifact: Record | None,
        state: ExperimentState | Record,
        status: str,
        pinned_content: Mapping[str, str | None],
    ) -> Record:
        if artifact is None:
            return {"status": "missing"}
        return {
            **self._document_identity(artifact),
            "status": self._report_status(
                state=state,
                artifact_id=str(artifact.get("id") or ""),
                status=status,
            ),
            "content": self._content(
                artifact=artifact, pinned_content=pinned_content
            )
            or "",
        }

    def _content(
        self,
        *,
        artifact: Record,
        pinned_content: Mapping[str, str | None],
    ) -> str | None:
        artifact_id = str(artifact.get("id") or "")
        if artifact_id in pinned_content:
            return pinned_content[artifact_id]
        return self.artifacts.submitted_text_for_artifact(artifact_id=artifact_id)

    @staticmethod
    def _document_identity(artifact: Record) -> Record:
        return {
            "id": artifact.get("id"),
            "path": artifact.get("path"),
            "submitted_at": _submitted_at(artifact),
        }

    @staticmethod
    def _artifact_reference(artifact: Record) -> Record:
        role = str(artifact.get("role") or "artifact").replace("_", " ")
        version = str(artifact.get("version") or "").strip()
        descriptor = f"{role} {version}".strip() if version else role
        return {
            "descriptor": descriptor,
            "id": artifact.get("id"),
            "path": artifact.get("path"),
            "submitted_at": _submitted_at(artifact),
        }

    @staticmethod
    def _plan_status(*, state: ExperimentState | Record, artifact_id: str) -> str:
        status = str(state.get("status") or "")
        if status == "design_review":
            return "in_review"
        review = _latest_review(
            state=state, role="design_reviewer", artifact_id=artifact_id
        )
        if review and str(review.get("verdict") or "") != "pass":
            return "changes_requested"
        if review and str(review.get("verdict") or "") == "pass":
            return "approved"
        if status in _PLAN_APPROVED_STATUSES and not _reviews_for_role(
            state=state, role="design_reviewer"
        ):
            # Compatibility for durable states written before reviews were
            # persisted. If reviews do exist but none pins this plan id, the
            # plan was resubmitted and is no longer approved.
            return "approved"
        return "submitted"

    @staticmethod
    def _report_status(
        *,
        state: ExperimentState | Record,
        artifact_id: str,
        status: str,
    ) -> str:
        if status == "experiment_review":
            return "in_review"
        review = _latest_review(
            state=state, role="experiment_reviewer", artifact_id=artifact_id
        )
        if review and str(review.get("verdict") or "") != "pass":
            return "changes_requested"
        if review and str(review.get("verdict") or "") == "pass":
            return "approved"
        if status == "complete" and not _reviews_for_role(
            state=state, role="experiment_reviewer"
        ):
            return "approved"
        return "submitted"


def _latest_review(
    *,
    state: ExperimentState | Record,
    role: str,
    artifact_id: str,
) -> Record | None:
    for review in state.get("reviews", []):
        if not isinstance(review, dict) or str(review.get("role") or "") != role:
            continue
        snapshot_id = str(review.get("target_snapshot_id") or "")
        if not snapshot_id or f"{artifact_id}:" in snapshot_id:
            return review
    return None


def _reviews_for_role(
    *, state: ExperimentState | Record, role: str
) -> list[Record]:
    return [
        review
        for review in state.get("reviews", [])
        if isinstance(review, dict) and str(review.get("role") or "") == role
    ]


def _submitted_at(artifact: Mapping[str, Any]) -> str:
    return str(
        artifact.get("submitted_at")
        or artifact.get("updated_at")
        or artifact.get("created_at")
        or ""
    )


def _document_summary(content: str | None, *, max_chars: int = 600) -> str:
    """Extract the authored Summary section, with a bounded legacy fallback."""

    text = str(content or "").strip()
    headings = list(
        re.finditer(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$", text, re.MULTILINE)
    )
    for index, heading in enumerate(headings):
        name = re.sub(r"[^a-z0-9]+", " ", heading.group(2).lower()).strip()
        if name != "summary":
            continue
        level = len(heading.group(1))
        end = len(text)
        for following in headings[index + 1 :]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        summary = text[heading.end() : end].strip()
        if summary:
            return _bounded_plain_text(summary, max_chars=max_chars)

    fallback = next(
        (
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ),
        "",
    )
    return _bounded_plain_text(fallback, max_chars=max_chars)


def _bounded_plain_text(value: str, *, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= max_chars:
        return compact
    clipped = compact[: max_chars - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0].rstrip()
    return clipped + "…"


__all__ = ["ExperimentContextQuery"]

"""Canonical bounded project context for agent operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from merv.shared.artifact_roles import PROJECT_GRAPH_ROLE
from merv.shared.content_summaries import content_tldr

from ..artifacts.ports import EvidenceReader
from ..research_core.facade import preferred_artifact


Record = dict[str, Any]

_PLAN_SUMMARY_STATUSES = frozenset(
    {"planned", "design_review", "ready_to_run", "running"}
)
_PROJECT_REFLECTION_ROLES = ("reflection_doc", PROJECT_GRAPH_ROLE)


class ProjectContextFacts(Protocol):
    def read(self, *, project_id: str | None = None) -> Record: ...


class ProjectContextQuery:
    """Compose one macro packet without hydrating rich child state."""

    def __init__(
        self, *, facts: ProjectContextFacts, evidence: EvidenceReader
    ) -> None:
        self.facts = facts
        self.evidence = evidence

    def build(self, *, project_id: str | None = None) -> Record:
        facts = self.facts.read(project_id=project_id)
        project = facts["project"]
        experiments = facts["experiments"]
        experiment_ids = tuple(str(row["id"]) for row in experiments)
        attempts = {
            str(row["id"]): int(row.get("attempt_index") or 0)
            for row in experiments
        }
        evidence = self.evidence.artifacts_for_targets(
            target_type="experiment",
            target_ids=experiment_ids,
            roles=("plan", "report"),
            attempt_indexes=attempts,
        )

        latest_published = facts.get("latest_published_reflection")
        reflection_evidence: Mapping[str, tuple[Any, ...]] = {}
        if isinstance(latest_published, dict):
            reflection_id = str(latest_published.get("id") or "")
            if reflection_id:
                reflection_evidence = self.evidence.artifacts_for_targets(
                    target_type="reflection",
                    target_ids=(reflection_id,),
                    roles=_PROJECT_REFLECTION_ROLES,
                    attempt_indexes={
                        reflection_id: int(
                            latest_published.get("attempt_index") or 0
                        )
                    },
                )

        return {
            "project": {
                "id": project.get("id"),
                "name": project.get("name"),
                "summary": project.get("summary", ""),
            },
            "reflection": self._reflection(
                latest=latest_published,
                open_wave=facts.get("open_reflection"),
                evidence=reflection_evidence,
            ),
            "literature": self._literature(facts),
            "claims": [dict(claim) for claim in facts["claims"]],
            "experiments": [
                self._experiment(
                    experiment=row,
                    evidence=evidence.get(str(row["id"]), ()),
                )
                for row in experiments
            ],
        }

    @staticmethod
    def _experiment(
        *, experiment: Record, evidence: tuple[Any, ...]
    ) -> Record:
        artifacts = [_artifact_record(item) for item in evidence]
        plan = preferred_artifact(artifacts=artifacts, roles=("plan",))
        report = preferred_artifact(artifacts=artifacts, roles=("report",))
        status = str(experiment.get("status") or "")
        preferred = plan if status in _PLAN_SUMMARY_STATUSES else report
        if preferred is None:
            preferred = report if status in _PLAN_SUMMARY_STATUSES else plan
        summary = str((preferred or {}).get("tldr") or "").strip()
        if not summary:
            summary = str(experiment.get("conclusion") or "").strip()
        if not summary:
            summary = str(experiment.get("intent") or "").strip()
        return {
            "id": experiment.get("id"),
            "name": experiment.get("name"),
            "status": status,
            "intent": experiment.get("intent"),
            "summary": summary,
            "tested_claim_ids": list(experiment.get("tested_claim_ids") or []),
            "updated_at": experiment.get("updated_at"),
        }

    @staticmethod
    def _reflection(
        *,
        latest: Record | None,
        open_wave: Record | None,
        evidence: Mapping[str, tuple[Any, ...]],
    ) -> Record:
        published = None
        if latest:
            reflection_id = str(latest.get("id") or "")
            artifacts = [
                _artifact_record(item)
                for item in evidence.get(reflection_id, ())
            ]
            document = preferred_artifact(
                artifacts=artifacts, roles=("reflection_doc",)
            )
            graph = preferred_artifact(
                artifacts=artifacts, roles=(PROJECT_GRAPH_ROLE,)
            )
            summary = str((document or {}).get("tldr") or "").strip()
            if not summary:
                summary = str(latest.get("title") or "").strip()
            published = {
                "id": latest.get("id"),
                "published_at": latest.get("published_at"),
                "summary": summary,
                "artifacts": [
                    ProjectContextQuery._artifact_reference(
                        artifact, descriptor=descriptor
                    )
                    for artifact, descriptor in (
                        (document, "reflection document"),
                        (graph, "project graph"),
                    )
                    if artifact is not None
                ],
            }
        open_context = (
            {
                "id": open_wave.get("id"),
                "title": open_wave.get("title"),
                "status": open_wave.get("status"),
                "updated_at": open_wave.get("updated_at"),
            }
            if open_wave
            else None
        )
        return {
            "latest_published": published,
            "open_wave": open_context,
        }

    @staticmethod
    def _literature(facts: Record) -> Record:
        source = facts.get("literature_summary") or {}
        summary = str(source.get("tldr") or "").strip()
        if not summary and str(source.get("body") or "").strip():
            summary = content_tldr(
                source.get("body"), role="literature_summary"
            )
        return {
            "summary": summary,
            "paper_count": int(facts.get("paper_count") or 0),
            "updated_at": source.get("updated_at") or None,
        }

    @staticmethod
    def _artifact_reference(
        artifact: Record, *, descriptor: str
    ) -> Record:
        return {
            "descriptor": descriptor,
            "id": artifact.get("id"),
            "path": artifact.get("path"),
            "submitted_at": (
                artifact.get("updated_at")
                or artifact.get("created_at")
                or ""
            ),
        }


def _artifact_record(evidence: Any) -> Record:
    """Project the public evidence value into selector/presentation fields."""

    return {
        "id": evidence.artifact_id,
        "role": evidence.role,
        "path": evidence.path,
        "attempt_index": evidence.attempt_index,
        "created_at": evidence.created_at,
        "updated_at": evidence.updated_at,
        "submitted_order": evidence.order,
        "tldr": evidence.tldr,
    }


__all__ = ["ProjectContextQuery"]

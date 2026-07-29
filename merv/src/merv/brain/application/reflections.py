"""Application-facing reflection commands and response presentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from merv.shared.content_summaries import content_tldr

from ..research_core import REFLECTION_WORKFLOW, Research
from .experiments.presentation import slim_review_rows
from .reflection_guidance import post_publish_guidance, present_reflection_signal

Record = dict[str, Any]


@dataclass(slots=True)
class ReflectionCommands:
    """Delegate reflection policy to Research and present its semantic checklist."""

    reflections: Research

    def create(
        self,
        *,
        project_id: str,
        title: str = "",
        lenses: list[Record] | None = None,
    ) -> Record:
        return present_agent_reflection_state(
            self.reflections.create_reflection(
                project_id=project_id, title=title, lenses=lenses or []
            ),
            include_content=False,
        )

    def get(
        self, *, project_id: str, reflection_id: str, include_content: bool = False
    ) -> Record:
        return present_agent_reflection_state(
            self.reflections.reflection_state(
                project_id=project_id,
                reflection_id=reflection_id,
                # Research hydrates once in a batch; presentation decides
                # whether the caller gets exact content or compact TLDRs.
                include_content=True,
            ),
            include_content=include_content,
        )

    def list(self, *, project_id: str) -> Record:
        result = self.reflections.list_reflections(project_id=project_id)
        return present_reflection_overview(
            {"count": result.get("count", len(result.get("reflections", []))), **result}
        )

    def transition(
        self, *, project_id: str, reflection_id: str, transition: str
    ) -> Record:
        return present_agent_reflection_state(
            self.reflections.transition_reflection(
                project_id=project_id,
                reflection_id=reflection_id,
                transition=transition,
            ),
            include_content=False,
        )


def present_reflection_state(state: Record) -> Record:
    result = dict(state)
    materialized = result.get("materialized_experiments")
    if result.get("status") == REFLECTION_WORKFLOW.success_status and materialized:
        items = list(result.items())
        index = list(result).index("materialized_experiments") + 1
        items.insert(index, ("post_publish_guidance", post_publish_guidance(
            materialized_experiments=materialized
        )))
        result = dict(items)
    return result


def present_agent_reflection_state(
    state: Record, *, include_content: bool = False
) -> Record:
    """Agent reflection state: TLDRs by default, exact documents on opt-in."""

    presented = present_reflection_state(state)
    if include_content:
        return presented
    result = dict(presented)
    result["reviews"] = slim_review_rows(result.get("reviews", []))
    result["current_attempt_artifacts"] = [
        _slim_content_artifact(artifact)
        for artifact in result.get("current_attempt_artifacts", [])
    ]
    corpus = dict(result.get("corpus") or {})
    corpus["previous_lens_reflections"] = {
        str(lens_id): _slim_content_artifact(artifact)
        for lens_id, artifact in (
            corpus.get("previous_lens_reflections") or {}
        ).items()
        if isinstance(artifact, dict)
    }
    corpus["previous_published_artifacts"] = {
        str(role): _slim_content_artifact(artifact)
        for role, artifact in (
            corpus.get("previous_published_artifacts") or {}
        ).items()
        if isinstance(artifact, dict)
    }
    corpus["terminal_experiments"] = [
        {
            **experiment,
            "artifacts": [
                _slim_content_artifact(artifact)
                for artifact in experiment.get("artifacts", [])
                if isinstance(artifact, dict)
            ],
        }
        for experiment in corpus.get("terminal_experiments", [])
        if isinstance(experiment, dict)
    ]
    result["corpus"] = corpus
    return result


def _slim_content_artifact(artifact: Record) -> Record:
    if "content" not in artifact:
        return dict(artifact)
    result: Record = {}
    for key, value in artifact.items():
        if key == "content":
            result["tldr"] = content_tldr(
                value,
                role=str(artifact.get("role") or ""),
                path=str(artifact.get("path") or ""),
            )
        else:
            result[key] = value
    return result


def present_reflection_overview(overview: Record) -> Record:
    state_keys = {"current", "open_reflection", "latest_published"}
    result = dict(overview)
    result["reflections"] = [
        present_reflection_state(item) for item in result.get("reflections", [])
    ]
    for key in state_keys:
        if isinstance(result.get(key), dict):
            result[key] = present_reflection_state(result[key])
    if isinstance(result.get("signal"), dict):
        result["signal"] = present_reflection_signal(result["signal"])
    return result


__all__ = [
    "ReflectionCommands",
    "present_agent_reflection_state",
    "present_reflection_overview",
    "present_reflection_state",
]

"""Remaining Surface wiring for the review-status feed advisory.

Tracking finalization now lives in Application and is covered by
tests/application/test_tracking_commands.py. This module characterizes the
still-unmigrated review.status attachment without a full review stack.
"""

from __future__ import annotations

import unittest
from typing import Any

from merv.brain.surface.tools.tool_handlers import build_control_tool_handlers


class _Unused:
    """Stands in for any service this test doesn't exercise: attribute
    access yields a callable that returns {} (see _HandlerTarget in
    test_tool_contracts.py — same idea, local copy to keep this file
    self-contained)."""

    def __getattr__(self, _name: str):
        def _handler(**_kwargs: Any) -> dict[str, Any]:
            return {}

        return _handler


class _StubFeed:
    """Records every feed_note_for call; returns a canned note unless the
    entity is muted, or raises if configured to (to exercise the "a feed
    hiccup must never block the workflow" rule)."""

    def __init__(self, *, muted: set[str] | None = None, raises: bool = False) -> None:
        self.muted = muted or set()
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, _name: str) -> Any:
        def _handler(**_kwargs: Any) -> dict[str, Any]:
            return {}

        return _handler

    def feed_note_for(self, *, project_id: str, entity_id: str, event: str) -> str | None:
        self.calls.append(
            {"project_id": project_id, "entity_id": entity_id, "event": event}
        )
        if self.raises:
            raise RuntimeError("feed hiccup")
        if entity_id in self.muted:
            return None
        return f"{entity_id} just had an update ({event})."


def _fallback_handler(**_kwargs: Any) -> dict[str, Any]:
    """Shared no-op for any method a stub below doesn't explicitly implement
    but that build_control_tool_handlers references eagerly (bare
    passthroughs like ``experiments.create`` in its handlers dict)."""
    return {}


class _StubExperiments:
    """State and run persistence needed by remaining Surface attach points."""

    def __init__(self, *, state: dict[str, Any]) -> None:
        self.state = state

    def __getattr__(self, _name: str) -> Any:
        return _fallback_handler

    def get_state(
        self, *, experiment_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        return dict(self.state)

    def record_mlflow_run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        run: dict[str, Any],
        event_type: str | None = None,
    ) -> dict[str, Any]:
        merged = dict(self.state)
        merged["mlflow_run"] = run
        return merged


class _BoomExperiments:
    """get_state always raises — simulates project_id resolution failing."""

    def __getattr__(self, _name: str) -> Any:
        return _fallback_handler

    def get_state(
        self, *, experiment_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        raise RuntimeError("boom")


class _StubReviews:
    def __init__(self, *, result: dict[str, Any]) -> None:
        self._result = result

    def __getattr__(self, _name: str) -> Any:
        return _fallback_handler

    def status(
        self, *, target_type: str, target_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        return dict(self._result)


def _target(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "workflow": _Unused(),
        "projects": _Unused(),
        "claims": _Unused(),
        "experiments": _Unused(),
        "reflection_tools": _Unused(),
        "resources": _Unused(),
        "storage": None,
        "reviews": _Unused(),
        "sandboxes": _Unused(),
        "feed": _Unused(),
        "experiment_transition": _Unused(),
        "experiment_exhibit": _Unused(),
        "tracking_context": _Unused(),
        "tracking_finalize": _Unused(),
    }
    base.update(overrides)
    return base


class ReviewStatusFeedNoteTest(unittest.TestCase):
    def test_attaches_note_when_a_verdict_exists_for_an_experiment(self) -> None:
        experiments = _StubExperiments(
            state={"id": "exp_9", "project_id": "proj_1", "status": "planned"}
        )
        reviews = _StubReviews(
            result={"requests": [], "reviews": [{"id": "rev_1", "verdict": "pass"}]}
        )
        feed = _StubFeed()
        handlers = build_control_tool_handlers(
            **_target(
                experiments=experiments, reviews=reviews, feed=feed
            )
        )
        result = handlers["review.status"](
            target_type="experiment", target_id="exp_9", project_id="proj_1"
        )
        self.assertIn("feed_note", result)
        self.assertEqual(
            feed.calls[-1],
            {
                "project_id": "proj_1",
                "entity_id": "exp_9",
                "event": "experiment_review_verdict",
            },
        )

    def test_no_note_while_review_is_only_pending(self) -> None:
        experiments = _StubExperiments(
            state={"id": "exp_10", "project_id": "proj_1", "status": "design_review"}
        )
        reviews = _StubReviews(
            result={"requests": [{"id": "req_1", "status": "requested"}], "reviews": []}
        )
        feed = _StubFeed()
        handlers = build_control_tool_handlers(
            **_target(
                experiments=experiments, reviews=reviews, feed=feed
            )
        )
        result = handlers["review.status"](
            target_type="experiment", target_id="exp_10", project_id="proj_1"
        )
        self.assertNotIn("feed_note", result)
        self.assertEqual(feed.calls, [])

    def test_no_note_for_non_experiment_targets(self) -> None:
        reviews = _StubReviews(
            result={"requests": [], "reviews": [{"id": "rev_2", "verdict": "pass"}]}
        )
        feed = _StubFeed()
        handlers = build_control_tool_handlers(
            **_target(reviews=reviews, feed=feed)
        )
        result = handlers["review.status"](
            target_type="reflection", target_id="syn_1", project_id="proj_1"
        )
        self.assertNotIn("feed_note", result)
        self.assertEqual(feed.calls, [])

    def test_project_id_resolution_failure_never_breaks_review_status(self) -> None:
        reviews = _StubReviews(
            result={"requests": [], "reviews": [{"id": "rev_3", "verdict": "pass"}]}
        )
        feed = _StubFeed()
        handlers = build_control_tool_handlers(
            **_target(
                experiments=_BoomExperiments(),
                reviews=reviews,
                feed=feed,
            )
        )
        result = handlers["review.status"](
            target_type="experiment", target_id="exp_11", project_id="proj_1"
        )
        self.assertNotIn("feed_note", result)
        self.assertEqual(result["reviews"][0]["id"], "rev_3")


if __name__ == "__main__":
    unittest.main()

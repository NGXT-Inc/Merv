from __future__ import annotations

import unittest
from copy import deepcopy

from merv.brain.application.experiments.presentation import (
    review_body,
    rich_experiment_state,
    slim_experiment_state,
    slim_review_rows,
)

TLDR_KEYS = {"id", "role", "verdict", "created_at", "synopsis"}
BODY_KEYS = TLDR_KEYS | {"findings", "notes", "evidence"}


def _review(review_id: str, *, created_at: str, **overrides) -> dict:
    return {
        "id": review_id,
        "role": "experiment_reviewer",
        "verdict": "pass",
        "created_at": created_at,
        "synopsis": f"synopsis for {review_id}",
        "findings": [{"issue": review_id}],
        "notes": f"notes for {review_id}",
        "evidence": {"exit_code": 0},
        "target_snapshot_id": "drop",
        **overrides,
    }


class ExperimentPresentationTest(unittest.TestCase):
    def test_rich_projection_omits_legacy_tracking_and_appends_storage(self) -> None:
        state = {
            "id": "exp_1",
            "current_attempt_artifacts": [],
            "mlflow_run": None,
            "reviews": [],
        }
        original = deepcopy(state)
        objects = [{"id": "so_1", "name": "model.bin"}]

        result = rich_experiment_state(state, storage_objects=objects)

        self.assertEqual(
            list(result),
            [
                "id",
                "current_attempt_artifacts",
                "reviews",
                "storage_objects",
            ],
        )
        self.assertNotIn("mlflow_run", result)
        self.assertEqual(result["storage_objects"], objects)
        self.assertEqual(state, original)

    def test_explicit_legacy_tracking_projection_preserves_compatibility_shape(
        self,
    ) -> None:
        state = {
            "id": "exp_1",
            "gate_checklist": {},
            "mlflow_run": {"run_id": "run_1"},
            "reviews": [],
        }
        objects = [{"id": "so_1", "name": "model.bin"}]

        rich = rich_experiment_state(
            state,
            storage_objects=objects,
            include_legacy_tracking=True,
        )
        slim = slim_experiment_state(
            state,
            storage_objects=objects,
            include_legacy_tracking=True,
        )

        self.assertLess(
            list(rich).index("storage_objects"),
            list(rich).index("mlflow_run"),
        )
        self.assertEqual(rich["mlflow_run"]["run_id"], "run_1")
        self.assertEqual(slim["mlflow_run"]["run_id"], "run_1")

    def test_agent_projection_preserves_exact_shape_and_prior_order(self) -> None:
        state = {
            "id": "exp_1",
            "name": "projection",
            "status": "running",
            "attempt_index": 2,
            "intent": "Keep substance.",
            "conclusion": "",
            "revision_context": "retry",
            "created_at": "created",
            "updated_at": "updated",
            "allowed_transitions": [{"transition": "submit_results"}],
            "gate_checklist": {"result": {"satisfied": False}},
            "mlflow_run": {"run_id": "run_1"},
            "claim_update_suggestions": [],
            "tested_claims": [
                {
                    "id": "claim_1",
                    "statement": "It works",
                    "confidence": "high",
                    "status": "active",
                    "scope": "project",
                    "private": "drop",
                }
            ],
            "artifacts": [
                {
                    "id": "art_old",
                    "role": "report",
                    "attempt_index": 1,
                    "path": "old.md",
                    "lens_id": "",
                    "tldr": "The earlier attempt missed the target.",
                },
                {
                    "id": "art_current",
                    "role": "report",
                    "attempt_index": 2,
                    "path": "report.md",
                    "lens_id": "",
                    "size_bytes": 12,
                    "title": "Report",
                    "tldr": "The retry met the target.",
                },
            ],
            "reviews": [
                {
                    "id": "rev_1",
                    "role": "experiment_reviewer",
                    "verdict": "pass",
                    "created_at": "reviewed",
                    "synopsis": "sound",
                    "findings": [],
                    "notes": "ok",
                    "evidence": {"exit_code": 0},
                    "target_snapshot_id": "drop",
                }
            ],
        }
        objects = [
            {
                "id": "so_1",
                "name": "model.bin",
                "version": 2,
                "kind": "model",
                "content_sha256": "a" * 64,
                "size_bytes": 4,
                "content_type": "application/octet-stream",
                "status": "available",
                "expires_at": None,
                "producing_run": "run_1",
                "source_uri": "",
                "notes": "kept",
                "created_at": "drop",
            }
        ]

        result = slim_experiment_state(state, storage_objects=objects)

        self.assertEqual(result["current_attempt_artifacts"], [
            {
                "id": "art_current",
                "role": "report",
                "path": "report.md",
                "lens_id": "",
                "size_bytes": 12,
                "title": "Report",
                "tldr": "The retry met the target.",
            }
        ])
        self.assertEqual(result["prior_attempt_artifacts"], [
            {
                "id": "art_old",
                "role": "report",
                "path": "old.md",
                "attempt_index": 1,
                "tldr": "The earlier attempt missed the target.",
            }
        ])
        self.assertEqual(set(result["storage_objects"][0]), {
            "id", "name", "version", "kind", "content_sha256", "size_bytes",
            "content_type", "status", "expires_at", "producing_run", "source_uri",
            "notes",
        })
        self.assertNotIn("target_snapshot_id", result["reviews"][0])

    def test_explicit_empty_current_resources_does_not_fall_back(self) -> None:
        state = {
            "id": "exp_1",
            "attempt_index": 1,
            "artifacts": [
                {
                    "id": "res_1",
                    "attempt_index": 1,
                    "role": "plan",
                }
            ],
            "current_attempt_artifacts": [],
        }

        result = slim_experiment_state(state, storage_objects=[])

        self.assertEqual(result["current_attempt_artifacts"], [])
        self.assertNotIn("prior_attempt_artifacts", result)


class ReviewDietTest(unittest.TestCase):
    def test_every_review_travels_as_a_tldr_by_default(self) -> None:
        rows = slim_review_rows(
            [
                _review("rev_3", created_at="2026-07-03T00:00:00Z"),
                _review("rev_2", created_at="2026-07-02T00:00:00Z"),
                _review("rev_1", created_at="2026-07-01T00:00:00Z"),
            ]
        )

        self.assertEqual([row["id"] for row in rows], ["rev_3", "rev_2", "rev_1"])
        self.assertEqual(set(rows[0]), TLDR_KEYS)
        self.assertEqual(set(rows[1]), TLDR_KEYS)
        self.assertEqual(set(rows[2]), TLDR_KEYS)
        self.assertEqual(rows[0]["synopsis"], "synopsis for rev_3")
        self.assertEqual(rows[1]["synopsis"], "synopsis for rev_2")

    def test_projection_preserves_authoritative_insertion_order(self) -> None:
        rows = slim_review_rows(
            [
                _review("rev_new", created_at="2026-07-01T00:00:00Z"),
                _review("rev_skewed", created_at="2026-07-09T00:00:00Z"),
                _review("rev_old", created_at="2026-06-20T00:00:00Z"),
            ]
        )

        self.assertEqual(
            [row["id"] for row in rows], ["rev_new", "rev_skewed", "rev_old"]
        )
        self.assertEqual(set(rows[0]), TLDR_KEYS)
        self.assertEqual(set(rows[1]), TLDR_KEYS)
        self.assertEqual(set(rows[2]), TLDR_KEYS)

    def test_same_timestamp_does_not_change_input_order(self) -> None:
        rows = slim_review_rows(
            [
                _review("rev_2", created_at="2026-07-01T00:00:00Z"),
                _review("rev_1", created_at="2026-07-01T00:00:00Z"),
            ]
        )

        self.assertEqual(set(rows[0]), TLDR_KEYS)
        self.assertEqual(set(rows[1]), TLDR_KEYS)

    def test_a_lone_review_is_still_tldr_only(self) -> None:
        rows = slim_review_rows([_review("rev_1", created_at="2026-07-01T00:00:00Z")])

        self.assertEqual(set(rows[0]), TLDR_KEYS)

    def test_no_reviews_projects_to_an_empty_list(self) -> None:
        self.assertEqual(slim_review_rows([]), [])

    def test_pre_synopsis_rows_borrow_their_first_notes_line(self) -> None:
        rows = slim_review_rows(
            [
                _review("rev_2", created_at="2026-07-02T00:00:00Z"),
                _review(
                    "rev_1",
                    created_at="2026-07-01T00:00:00Z",
                    synopsis="",
                    notes="\n\n  The sweep never separated the arms.  \nmore detail",
                ),
            ]
        )

        self.assertEqual(rows[1]["synopsis"], "The sweep never separated the arms.")

    def test_borrowed_notes_line_is_truncated_to_the_synopsis_ceiling(self) -> None:
        rows = slim_review_rows(
            [
                _review("rev_2", created_at="2026-07-02T00:00:00Z"),
                _review(
                    "rev_1",
                    created_at="2026-07-01T00:00:00Z",
                    synopsis="",
                    notes="w" * 900,
                ),
            ]
        )

        self.assertEqual(len(rows[1]["synopsis"]), 420)
        self.assertTrue(rows[1]["synopsis"].endswith("…"))

    def test_missing_synopsis_and_notes_falls_back_to_a_finding(self) -> None:
        rows = slim_review_rows(
            [
                _review("rev_2", created_at="2026-07-02T00:00:00Z"),
                _review(
                    "rev_1",
                    created_at="2026-07-01T00:00:00Z",
                    synopsis="",
                    notes="   \n \n",
                ),
            ]
        )

        self.assertEqual(rows[1]["synopsis"], "Review finding: rev_1")

    def test_fully_empty_legacy_review_gets_a_nonempty_status_tldr(self) -> None:
        rows = slim_review_rows(
            [
                _review(
                    "rev_1",
                    created_at="2026-07-01T00:00:00Z",
                    synopsis="",
                    notes="",
                    findings=[],
                ),
            ]
        )

        self.assertIn("returned pass", rows[0]["synopsis"])
        self.assertLessEqual(len(rows[0]["synopsis"]), 420)

    def test_review_body_reads_an_older_round_back_with_its_routing(self) -> None:
        reviews = [
            _review("rev_2", created_at="2026-07-02T00:00:00Z"),
            _review("rev_1", created_at="2026-07-01T00:00:00Z", return_to="planned"),
        ]

        body = review_body(reviews, review_id="rev_1")

        self.assertEqual(set(body), BODY_KEYS | {"return_to"})
        self.assertEqual(body["notes"], "notes for rev_1")
        self.assertEqual(body["return_to"], "planned")
        self.assertIsNone(review_body(reviews, review_id="rev_9"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

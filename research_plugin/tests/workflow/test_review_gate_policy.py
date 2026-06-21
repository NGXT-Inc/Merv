from __future__ import annotations

import unittest

from backend.domain.review_gates import (
    REVIEW_GATE_EXEMPT_ROLES,
    REVIEW_GATE_ROLES,
    expected_review_gate_role,
    is_review_gate_exempt,
)


class ReviewGatePolicyTest(unittest.TestCase):
    def test_gate_roles_are_explicit_table_entries(self) -> None:
        self.assertEqual(
            REVIEW_GATE_ROLES,
            {
                ("experiment", "design_review"): "design_reviewer",
                ("experiment", "experiment_review"): "experiment_reviewer",
                ("synthesis", "synthesis_review"): "reflection_reviewer",
            },
        )

    def test_expected_role_returns_none_outside_review_gates(self) -> None:
        self.assertEqual(
            expected_review_gate_role(
                target_type="experiment",
                target_status="design_review",
            ),
            "design_reviewer",
        )
        self.assertIsNone(
            expected_review_gate_role(
                target_type="experiment",
                target_status="running",
            )
        )

    def test_human_and_automated_checks_are_gate_exempt(self) -> None:
        self.assertEqual(REVIEW_GATE_EXEMPT_ROLES, {"human", "automated_check"})
        self.assertTrue(is_review_gate_exempt(role="human"))
        self.assertTrue(is_review_gate_exempt(role="automated_check"))
        self.assertFalse(is_review_gate_exempt(role="design_reviewer"))


if __name__ == "__main__":
    unittest.main()

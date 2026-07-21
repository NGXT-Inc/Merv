from __future__ import annotations

import unittest

from merv.brain.research_core.domain import review_gates
from merv.brain.research_core.domain.review_gates import (
    REVIEW_GATE_EXEMPT_ROLES,
    is_review_gate_exempt,
)
from merv.brain.research_core.domain.reflection_gates import REFLECTION_GATE_TABLE
from merv.brain.research_core.domain.workflow_gates import GATE_TABLE


def _review_roles_from_gate_tables() -> dict[tuple[str, str], str]:
    rows: dict[tuple[str, str], str] = {}
    for status, forward in GATE_TABLE.items():
        if forward.review is not None:
            rows[("experiment", status)] = forward.review.role
    for status, forward in REFLECTION_GATE_TABLE.items():
        if forward.review is not None:
            rows[("reflection", status)] = forward.review.role
    return rows


class ReviewGatePolicyTest(unittest.TestCase):
    def test_gate_roles_are_explicit_table_entries(self) -> None:
        self.assertEqual(
            _review_roles_from_gate_tables(),
            {
                ("experiment", "design_review"): "design_reviewer",
                ("experiment", "experiment_review"): "experiment_reviewer",
                ("reflection", "reflection_review"): "reflection_reviewer",
            },
        )

    def test_gate_roles_have_no_parallel_registry(self) -> None:
        self.assertFalse(hasattr(review_gates, "REVIEW_GATE_ROLES"))
        self.assertFalse(hasattr(review_gates, "expected_review_gate_role"))

    def test_non_review_states_have_no_review_requirement(self) -> None:
        self.assertIsNone(GATE_TABLE["running"].review)
        self.assertIsNone(REFLECTION_GATE_TABLE["reflecting"].review)
        self.assertIsNone(REFLECTION_GATE_TABLE["synthesizing"].review)

    def test_human_and_automated_checks_are_gate_exempt(self) -> None:
        self.assertEqual(REVIEW_GATE_EXEMPT_ROLES, {"human", "automated_check"})
        self.assertTrue(is_review_gate_exempt(role="human"))
        self.assertTrue(is_review_gate_exempt(role="automated_check"))
        self.assertFalse(is_review_gate_exempt(role="design_reviewer"))


if __name__ == "__main__":
    unittest.main()

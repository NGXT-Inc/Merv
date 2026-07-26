from __future__ import annotations

import unittest

from merv.brain.sandbox.lifecycle_reducer import (
    reap_decision,
    reconcile_decision,
    release_decision,
)


class SandboxLifecycleReducerTest(unittest.TestCase):
    def test_unknown_liveness_never_terminates(self) -> None:
        decision = reconcile_decision(
            row={"status": "running", "sandbox_id": "sb", "sandbox_uid": "uid"},
            alive=None,
        )
        self.assertEqual(decision.intents, ())
        self.assertIsNone(decision.event)

    def test_confirmed_dead_row_marks_and_emits(self) -> None:
        decision = reconcile_decision(
            row={"status": "running", "sandbox_id": "sb", "sandbox_uid": "uid"},
            alive=False,
        )
        self.assertEqual([item.kind for item in decision.intents], ["mark_terminated"])
        self.assertEqual(decision.event.type, "sandbox.expired")

    def test_confirmed_cleanup_lets_a_wedged_provision_fail(self) -> None:
        decision = reconcile_decision(
            row={"status": "provisioning", "sandbox_uid": "uid"},
            alive=None,
            job_live=False,
            cleanup="gone",
        )
        self.assertEqual([item.kind for item in decision.intents], ["mark_failed"])
        self.assertEqual(decision.event.type, "sandbox.failed")

    def test_unconfirmed_cleanup_parks_a_wedged_provision(self) -> None:
        decision = reconcile_decision(
            row={"status": "provisioning", "sandbox_uid": "uid"},
            alive=None,
            job_live=False,
            cleanup="maybe_alive",
        )
        self.assertEqual(
            [item.kind for item in decision.intents], ["mark_cleanup_pending"]
        )
        self.assertEqual(decision.event.type, "sandbox.cleanup_pending")
        # The verdict the row was headed for survives the detour, so the retry
        # that finally confirms the delete lands on `failed`, not `terminated`.
        self.assertIn("provisioning interrupted", decision.intents[0].payload["error"])

    def test_a_caller_that_skipped_the_cleanup_gets_the_safe_answer(self) -> None:
        decision = reconcile_decision(
            row={"status": "provisioning", "sandbox_uid": "uid"},
            alive=None,
            job_live=False,
        )
        self.assertEqual(
            [item.kind for item in decision.intents], ["mark_cleanup_pending"]
        )

    def test_failed_termination_parks_the_reap_instead_of_terminalizing(self) -> None:
        decision = reap_decision(
            row={"sandbox_id": "sb", "sandbox_uid": "uid"},
            outcome="maybe_alive",
            event_type="sandbox.expired",
        )
        self.assertEqual(
            [item.kind for item in decision.intents], ["mark_cleanup_pending"]
        )
        self.assertEqual(decision.event.type, "sandbox.cleanup_pending")
        self.assertEqual(decision.event.payload["trigger"], "expired")

    def test_release_marks_only_after_provider_confirmation(self) -> None:
        uncertain = release_decision(
            row={"sandbox_id": "sb", "sandbox_uid": "uid"},
            outcome="maybe_alive",
            active_experiment_ids=["exp_1"],
        )
        confirmed = release_decision(
            row={"sandbox_id": "sb", "sandbox_uid": "uid"},
            outcome="stopped",
            active_experiment_ids=["exp_1"],
        )
        self.assertEqual(
            [item.kind for item in uncertain.intents], ["mark_cleanup_pending"]
        )
        self.assertEqual(uncertain.event.type, "sandbox.cleanup_pending")
        self.assertEqual(uncertain.event.payload["trigger"], "release")
        self.assertEqual(confirmed.intents[0].kind, "mark_terminated")
        self.assertEqual(confirmed.event.type, "sandbox.released")


if __name__ == "__main__":
    unittest.main()

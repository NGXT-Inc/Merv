"""The one gateway: every receipt read is deduped, serialized, and capped.

Behavior of the read itself belongs to test_sandbox_runs.py; this file pins
what the observer adds around it — one box asked once per freshness window,
one read at a time per sandbox, a bounded number of boxes read at once, and
who is allowed to wait for a slot.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from merv.brain.sandbox.execution.backends.fake import FakeSandboxBackend
from merv.brain.sandbox.runs_observer import RunsObserver
from tests.support.brain import TestBrain


def _row(uid: str = "sbx-1", status: str = "running") -> dict:
    return {
        "sandbox_uid": uid,
        "sandbox_id": f"vm-{uid}",
        "status": status,
        "project_id": "proj",
    }


class _StubLedger:
    """Counts remote reads and, on request, holds one open mid-flight."""

    def __init__(self) -> None:
        self.calls = 0
        self.verdict = True
        self.hold = False
        self.entered = threading.Event()
        self.finish = threading.Event()
        self._guard = threading.Lock()

    def reconcile_row(self, *, row: dict) -> bool:
        with self._guard:
            self.calls += 1
        self.entered.set()
        if self.hold:
            self.finish.wait(timeout=5.0)
        return self.verdict


class _StubRepository:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    def list_running_rows(self) -> list[dict]:
        return list(self.rows)


class RunsObserverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = _StubLedger()
        self.repository = _StubRepository([_row()])
        self.observer = RunsObserver(
            ledger=self.ledger, repository=self.repository, concurrency=4
        )

    def _in_thread(self, target) -> threading.Thread:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2.0)
        return thread

    # ---------- freshness ----------

    def test_a_read_inside_the_window_is_not_repeated(self) -> None:
        self.assertTrue(self.observer.observe(row=_row(), max_age_seconds=60))
        self.assertTrue(self.observer.observe(row=_row(), max_age_seconds=60))
        self.assertEqual(self.ledger.calls, 1)

    def test_the_sweep_reuses_the_read_a_poller_just_paid_for(self) -> None:
        self.assertTrue(self.observer.observe(row=_row(), max_age_seconds=5))
        self.assertEqual(self.observer.observe_live(), 1)
        self.assertEqual(self.ledger.calls, 1)

    def test_a_window_of_zero_always_asks(self) -> None:
        self.observer.observe(row=_row(), max_age_seconds=0)
        self.observer.observe(row=_row(), max_age_seconds=0)
        self.assertEqual(self.ledger.calls, 2)

    def test_a_failed_read_is_never_remembered_as_fresh(self) -> None:
        # Only a SUCCESSFUL read stamps; otherwise one dead channel would hold
        # the mirror stale for the whole window.
        self.ledger.verdict = False
        self.assertFalse(self.observer.observe(row=_row(), max_age_seconds=60))
        self.assertFalse(self.observer.observe(row=_row(), max_age_seconds=60))
        self.assertEqual(self.ledger.calls, 2)

    def test_force_always_asks_the_box(self) -> None:
        self.assertTrue(self.observer.observe(row=_row(), max_age_seconds=60))
        self.assertTrue(self.observer.observe_forced(row=_row()))
        self.assertEqual(self.ledger.calls, 2)

    def test_a_row_that_left_running_is_never_answered_from_the_stamp(self) -> None:
        self.assertTrue(self.observer.observe(row=_row(), max_age_seconds=60))
        self.ledger.verdict = False  # the ledger refuses a row that is not active
        self.assertFalse(
            self.observer.observe(row=_row(status="terminated"), max_age_seconds=60)
        )
        self.assertEqual(self.ledger.calls, 2)

    # ---------- one box, one read ----------

    def test_concurrent_observers_of_one_box_share_a_single_read(self) -> None:
        self.ledger.hold = True
        answers: list[bool] = []
        guard = threading.Lock()

        def poll() -> None:
            answer = self.observer.observe(row=_row(), max_age_seconds=60)
            with guard:
                answers.append(answer)

        first = self._in_thread(poll)
        self.assertTrue(self.ledger.entered.wait(timeout=2.0))
        followers = [self._in_thread(poll) for _ in range(3)]
        self.ledger.finish.set()
        for thread in (first, *followers):
            thread.join(timeout=5.0)
        self.assertEqual(answers, [True] * 4)
        self.assertEqual(self.ledger.calls, 1)

    def test_a_forced_read_behind_an_in_flight_one_still_asks(self) -> None:
        # The in-flight read stamps the box just before the forced caller gets
        # the lock; a pre-destruction read must not inherit that stamp.
        self.ledger.hold = True
        forced: list[bool] = []
        self._in_thread(lambda: self.observer.observe(row=_row(), max_age_seconds=60))
        self.assertTrue(self.ledger.entered.wait(timeout=2.0))
        waiter = self._in_thread(
            lambda: forced.append(self.observer.observe_forced(row=_row()))
        )
        self.ledger.hold = False
        self.ledger.finish.set()
        waiter.join(timeout=5.0)
        self.assertEqual(forced, [True])
        self.assertEqual(self.ledger.calls, 2)


class RunsObserverConcurrencyCapTest(unittest.TestCase):
    """One permit, so the second reader of a DIFFERENT box has to wait."""

    def setUp(self) -> None:
        self.ledger = _StubLedger()
        self.observer = RunsObserver(
            ledger=self.ledger, repository=_StubRepository(), concurrency=1
        )
        self.ledger.hold = True
        self.occupied = threading.Thread(
            target=lambda: self.observer.observe(
                row=_row("busy"), max_age_seconds=60
            ),
            daemon=True,
        )
        self.occupied.start()
        self.assertTrue(self.ledger.entered.wait(timeout=2.0))

    def tearDown(self) -> None:
        self.ledger.finish.set()
        self.occupied.join(timeout=5.0)

    def test_a_poller_that_cannot_get_a_slot_skips_without_stamping(self) -> None:
        self.assertFalse(self.observer.observe(row=_row(), max_age_seconds=0.05))
        self.assertEqual(self.ledger.calls, 1)
        # Nothing was stamped, so the next pass is a real read once a slot frees.
        self.ledger.hold = False
        self.ledger.finish.set()
        self.occupied.join(timeout=5.0)
        self.assertTrue(self.observer.observe(row=_row(), max_age_seconds=60))
        self.assertEqual(self.ledger.calls, 2)

    def test_a_bounded_forced_read_gives_up_rather_than_delay_a_release(self) -> None:
        # The release path's outcome on timeout is the failed-SSH-read outcome:
        # False, nothing stamped, and teardown proceeds without the observation.
        self.assertFalse(
            self.observer.observe_forced(row=_row(), acquire_timeout=0.05)
        )
        self.assertEqual(self.ledger.calls, 1)
        self.ledger.hold = False
        self.ledger.finish.set()
        self.occupied.join(timeout=5.0)
        self.assertTrue(self.observer.observe(row=_row(), max_age_seconds=60))
        self.assertEqual(self.ledger.calls, 2)

    def test_an_unbounded_forced_read_waits_for_its_slot(self) -> None:
        # The reaper's loop: correctness over latency, so it blocks instead of
        # deciding a box's fate on an unread receipt.
        answers: list[bool] = []
        waiter = threading.Thread(
            target=lambda: answers.append(self.observer.observe_forced(row=_row())),
            daemon=True,
        )
        waiter.start()
        waiter.join(timeout=0.3)
        self.assertTrue(waiter.is_alive(), "forced daemon read did not wait")
        self.assertEqual(self.ledger.calls, 1)
        self.ledger.hold = False
        self.ledger.finish.set()
        waiter.join(timeout=5.0)
        self.assertEqual(answers, [True])
        self.assertEqual(self.ledger.calls, 2)


class RunsObserverWiringTest(unittest.TestCase):
    """Every path that reads receipts holds the observer, not the ledger."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        repo = Path(self.tmp.name)
        self.app = TestBrain(
            repo_root=repo,
            db_path=repo / ".research_plugin" / "state.sqlite",
            execution_backend=FakeSandboxBackend(),
        )

    def tearDown(self) -> None:
        self.app.shutdown()
        self.tmp.cleanup()

    def test_every_receipt_path_runs_through_the_observer(self) -> None:
        runtime = self.app.sandbox_runtime
        observer = runtime.runs_observer
        self.assertIs(observer.ledger, runtime.runs)
        self.assertIs(observer.repository, runtime.repository)
        self.assertEqual(runtime.lifecycle.observe_runs, observer.observe_forced)
        self.assertEqual(runtime.daemons.reconcile_runs, observer.observe_live)
        self.assertEqual(
            runtime.daemons.heartbeat.refresh_runs, observer.observe_forced
        )
        self.assertIs(self.app.sandboxes.runs_observer, observer)
        # The ledger's own fan-out and pre-terminal wrappers are gone with it:
        # a second way in is a second way to read the same box twice.
        self.assertFalse(hasattr(runtime.runs, "reconcile_live"))
        self.assertFalse(hasattr(runtime.runs, "final_observe"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path

from merv.brain.application.events import EventDispatcher, EventReaction
from merv.brain.kernel.state.store import StateStore
from merv.brain.research_core.experiments import ExperimentService


class CommittedEventTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(db_path=Path(self.tmp.name) / "state.sqlite")
        with closing(self.store.connect()) as conn:
            row = conn.execute("SELECT id FROM projects").fetchone()
            assert row is not None
            self.project_id = str(row["id"])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_record_event_returns_exact_row_and_deep_frozen_payload(self) -> None:
        payload = {"z": [{"nested": "original"}], "a": 1}
        with self.store.transaction() as conn:
            event = self.store.record_event(
                conn=conn,
                project_id=self.project_id,
                event_type="test.recorded",
                target_type="test",
                target_id="target_1",
                payload=payload,
            )
            payload["z"][0]["nested"] = "caller-mutated"

        with closing(self.store.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE id = ?", (event.id,)
            ).fetchone()
        assert row is not None
        self.assertEqual(str(row["payload_json"]), '{"a": 1, "z": [{"nested": "original"}]}')
        self.assertEqual(event.project_id, str(row["project_id"]))
        self.assertEqual(event.type, str(row["type"]))
        self.assertEqual(event.target_type, str(row["target_type"]))
        self.assertEqual(event.target_id, str(row["target_id"]))
        self.assertEqual(event.created_at, str(row["created_at"]))
        self.assertEqual(event.payload["z"][0]["nested"], "original")
        self.assertIsInstance(event.payload["z"], tuple)
        with self.assertRaises(TypeError):
            event.payload["new"] = "nope"
        with self.assertRaises(TypeError):
            event.payload["z"][0]["nested"] = "nope"
        with self.assertRaises(FrozenInstanceError):
            event.type = "changed"

        wire = self.store.events_since(
            project_id=self.project_id, after_id=event.id - 1
        )["events"]
        self.assertEqual(
            wire,
            [
                {
                    "id": event.id,
                    "project_id": self.project_id,
                    "type": "test.recorded",
                    "target_type": "test",
                    "target_id": "target_1",
                    "created_at": event.created_at,
                    "payload": {"a": 1, "z": [{"nested": "original"}]},
                }
            ],
        )
        wire[0]["payload"]["z"][0]["nested"] = "wire-remains-mutable"
        self.assertEqual(event.payload["z"][0]["nested"], "original")

    def test_transition_variant_returns_committed_event_and_legacy_returns_state(self) -> None:
        experiments = ExperimentService(store=self.store)
        created = experiments.create(
            project_id=self.project_id, name="committed-event", intent="test"
        )
        committed = experiments.transition_with_event(
            project_id=self.project_id,
            experiment_id=created["id"],
            transition="mark_failed",
            evidence={"reason": "expected failure", "codes": [1, 2]},
        )
        state, event = committed.state, committed.event
        with self.assertRaises(FrozenInstanceError):
            committed.event = event
        self.assertEqual(state["status"], "failed")
        self.assertEqual(event.type, "experiment.transitioned")
        self.assertEqual(event.target_id, created["id"])
        self.assertEqual(
            dict(event.payload),
            {
                "evidence": {"codes": (1, 2), "reason": "expected failure"},
                "from": "planned",
                "to": "failed",
                "transition": "mark_failed",
            },
        )

        dispatcher = EventDispatcher()

        def committed_probe(context):
            with closing(self.store.connect()) as conn:
                row = conn.execute(
                    "SELECT payload_json FROM events WHERE id = ?", (context.event.id,)
                ).fetchone()
            assert row is not None
            self.assertEqual(json.loads(str(row["payload_json"])), {
                "evidence": {"codes": [1, 2], "reason": "expected failure"},
                "from": "planned",
                "to": "failed",
                "transition": "mark_failed",
            })
            return EventReaction(state=context.state, value="readable")

        dispatcher.register(
            event_type=event.type,
            phase="post_commit",
            name="probe",
            handler=committed_probe,
        )
        dispatched = dispatcher.dispatch(event=event, phase="post_commit", state=state)
        self.assertIs(dispatched.state, state)
        self.assertEqual(dict(dispatched.outcomes), {"probe": "readable"})

        legacy_created = experiments.create(
            project_id=self.project_id, name="legacy-transition", intent="test"
        )
        legacy_state = experiments.transition(
            project_id=self.project_id,
            experiment_id=legacy_created["id"],
            transition="abandon",
        )
        self.assertIsInstance(legacy_state, dict)
        self.assertEqual(legacy_state["status"], "abandoned")

    def test_event_insert_failure_rolls_back_state_and_event_together(self) -> None:
        experiments = ExperimentService(store=self.store)
        created = experiments.create(
            project_id=self.project_id, name="rollback-event", intent="test"
        )
        with self.store.transaction() as conn:
            conn.execute(
                """
                CREATE TRIGGER reject_transition_event
                BEFORE INSERT ON events
                WHEN NEW.type = 'experiment.transitioned'
                BEGIN
                  SELECT RAISE(ABORT, 'forced event failure');
                END
                """
            )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "forced event failure"):
            experiments.transition_with_event(
                project_id=self.project_id,
                experiment_id=created["id"],
                transition="mark_failed",
            )

        state = experiments.get_state(
            project_id=self.project_id, experiment_id=created["id"]
        )
        self.assertEqual(state["status"], "planned")
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM events
                WHERE type = 'experiment.transitioned' AND target_id = ?
                """,
                (created["id"],),
            ).fetchone()
        assert row is not None
        self.assertEqual(int(row["count"]), 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from merv.brain.application.events import (
    EventCatalogEntry,
    EventDispatcher,
    EventReaction,
)
from merv.brain.artifacts.submissions import ArtifactSubmissionService
from merv.brain.kernel.state.store import StateStore
from merv.brain.research_core.association_targets import AssociationTargets
from merv.brain.research_core.experiments import (
    TRACKING_EVENT_TYPES,
    ExperimentService,
)
from merv.brain.research_core.facade import ResearchCoreFacade


_TRANSITION_PRODUCER = (
    "merv.brain.research_core.experiments.ExperimentService."
    "transition_with_event"
)


class CommittedEventTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(db_path=Path(self.tmp.name) / "state.sqlite")
        self.evidence = ArtifactSubmissionService(
            store=self.store,
            association_targets=AssociationTargets(store=self.store),
        )
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
        experiments = ExperimentService(
            store=self.store, evidence_reader=self.evidence,
            submissions=self.evidence
        )
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

        dispatcher.bind_catalog(
            (
                EventCatalogEntry(
                    producer=_TRANSITION_PRODUCER,
                    event_type=event.type,
                    payload_version=1,
                    transaction_boundary=_TRANSITION_PRODUCER,
                    reaction_phase="post_commit",
                    handler_identity="probe",
                    failure="fatal",
                    idempotency="repeat_safe",
                ),
            ),
            handlers={"probe": committed_probe},
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

    def test_tracking_refresh_returns_the_exact_committed_ledger_event(self) -> None:
        experiments = ExperimentService(
            store=self.store, evidence_reader=self.evidence,
            submissions=self.evidence
        )
        research = ResearchCoreFacade(experiments)
        created = experiments.create(
            project_id=self.project_id, name="tracking-event", intent="test"
        )
        run = {
            "run_id": "run_1",
            "run_name": "owned",
            "status": "FINISHED",
            "artifact_uri": "s3://tracking/run_1",
            "created_at": "2026-07-19T18:00:00Z",
        }

        committed = research.refresh_tracking_run(
            project_id=self.project_id,
            experiment_id=created["id"],
            run=run,
        )

        with closing(self.store.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE id = ?", (committed.event.id,)
            ).fetchone()
        assert row is not None
        self.assertEqual(committed.event.type, "experiment.mlflow_run_refreshed")
        self.assertEqual(committed.event.target_id, created["id"])
        self.assertEqual(committed.event.created_at, str(row["created_at"]))
        self.assertEqual(
            dict(committed.event.payload), json.loads(str(row["payload_json"]))
        )
        self.assertEqual(committed.state["mlflow_run"]["run_id"], "run_1")
        self.assertEqual(committed.state["mlflow_run"]["status"], "FINISHED")

    def test_event_insert_failure_rolls_back_state_and_event_together(self) -> None:
        experiments = ExperimentService(
            store=self.store, evidence_reader=self.evidence,
            submissions=self.evidence
        )
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


class TrackingDeliveryLedgerSqlTest(unittest.TestCase):
    """The delivery barrier over the real SQL, not an in-memory stand-in.

    The application tests prove the handler's decisions; only these prove the
    query behind them, so a filter or ordering regression cannot duplicate a
    durable event while every lost-ack test stays green.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(db_path=Path(self.tmp.name) / "state.sqlite")
        evidence = ArtifactSubmissionService(
            store=self.store,
            association_targets=AssociationTargets(store=self.store),
        )
        self.experiments = ExperimentService(
            store=self.store, evidence_reader=evidence, submissions=evidence
        )
        with closing(self.store.connect()) as conn:
            row = conn.execute("SELECT id FROM projects").fetchone()
            assert row is not None
            self.project_id = str(row["id"])
        self.experiment_id = self.experiments.create(
            project_id=self.project_id, name="delivery-ledger", intent="test"
        )["id"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _record(self, *, run_id: str, delivery_id: int | None) -> dict:
        return self.experiments.record_mlflow_run(
            project_id=self.project_id,
            experiment_id=self.experiment_id,
            run={"run_id": run_id, "status": "RUNNING"},
            delivery_id=delivery_id,
        )

    def _tracking_events(self) -> list[dict]:
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                "SELECT type, payload_json FROM events WHERE target_id = ?"
                "  AND type IN (?, ?, ?) ORDER BY id",
                (self.experiment_id, *TRACKING_EVENT_TYPES),
            ).fetchall()
        return [
            {"type": str(row["type"]), **json.loads(str(row["payload_json"]))}
            for row in rows
        ]

    def test_sql_lookup_finds_the_delivery_and_the_writer_no_ops_on_a_duplicate(
        self,
    ) -> None:
        self._record(run_id="run_a", delivery_id=41)

        found = self.experiments.tracking_delivery_state(
            project_id=self.project_id, experiment_id=self.experiment_id,
            delivery_id=41,
        )
        assert found is not None
        self.assertEqual(found["mlflow_run"]["run_id"], "run_a")
        # A delivery that never wrote is absent: the query correlates on the
        # payload key, not on "this experiment has some tracking event".
        self.assertIsNone(
            self.experiments.tracking_delivery_state(
                project_id=self.project_id, experiment_id=self.experiment_id,
                delivery_id=42,
            )
        )

        # The duplicate carries a different run so a no-op is distinguishable
        # from a re-write that happens to land the same values.
        replayed = self._record(run_id="run_replayed", delivery_id=41)

        self.assertEqual(replayed["mlflow_run"]["run_id"], "run_a")
        # The event-returning shape stays total across the no-op: it answers
        # with the event that landed rather than inventing one.
        refreshed = self.experiments.record_mlflow_run(
            project_id=self.project_id,
            experiment_id=self.experiment_id,
            run={"run_id": "run_replayed", "status": "RUNNING"},
            delivery_id=41,
            return_event=True,
        )
        self.assertEqual(refreshed.event.payload["run_id"], "run_a")
        self.assertEqual(refreshed.event.payload["delivery_id"], 41)
        self.assertEqual(
            self._tracking_events(),
            [
                {
                    "type": "experiment.mlflow_run_created",
                    "run_id": "run_a", "run_name": "", "status": "RUNNING",
                    "error": "", "previous_run_id": "", "delivery_id": 41,
                }
            ],
        )

    def test_a_rival_write_after_a_negative_read_cannot_duplicate_the_append(
        self,
    ) -> None:
        # The race the barrier exists for: A's ledger read (its own
        # transaction) finds nothing, A's write commits late anyway, a newer
        # delivery B commits on top, and only then does A retry.
        self.assertIsNone(
            self.experiments.tracking_delivery_state(
                project_id=self.project_id, experiment_id=self.experiment_id,
                delivery_id=41,
            )
        )
        self._record(run_id="run_a", delivery_id=41)  # A's delayed commit
        self._record(run_id="run_rival", delivery_id=99)  # B, the newer outcome

        retried = self._record(run_id="run_a", delivery_id=41)

        # One append per delivery, and A's older outcome does not come back.
        self.assertEqual(
            [(event["delivery_id"], event["run_id"]) for event in self._tracking_events()],
            [(41, "run_a"), (99, "run_rival")],
        )
        self.assertEqual(retried["mlflow_run"]["run_id"], "run_rival")

    def test_an_unkeyed_write_is_never_deduplicated(self) -> None:
        # refresh_tracking_run carries no delivery id; the barrier must not
        # collapse those writes into the first one.
        self._record(run_id="run_a", delivery_id=None)
        self._record(run_id="run_b", delivery_id=None)

        self.assertEqual(
            [event["run_id"] for event in self._tracking_events()],
            ["run_a", "run_b"],
        )

    def test_the_ledger_scan_reads_a_bounded_window(self) -> None:
        self._record(run_id="run_a", delivery_id=41)
        self._record(run_id="run_b", delivery_id=42)

        # Proof the LIMIT is really in the query: shrink the window and the
        # older delivery falls outside it. Production's 200 is far past any
        # plausible burst of tracking events for one experiment.
        with patch(
            "merv.brain.research_core.experiments.TRACKING_DELIVERY_SCAN_LIMIT", 1
        ):
            self.assertIsNone(
                self.experiments.tracking_delivery_state(
                    project_id=self.project_id, experiment_id=self.experiment_id,
                    delivery_id=41,
                )
            )
            self.assertIsNotNone(
                self.experiments.tracking_delivery_state(
                    project_id=self.project_id, experiment_id=self.experiment_id,
                    delivery_id=42,
                )
            )


if __name__ == "__main__":
    unittest.main()

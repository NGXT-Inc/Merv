from __future__ import annotations

import unittest

from merv.brain.application.events import (
    EventCatalogEntry,
    EventDispatcher,
    EventReaction,
)
from merv.brain.kernel.events import StoredEvent, freeze_json_object


_TRANSITION_PRODUCER = (
    "merv.brain.research_core.experiments.ExperimentService."
    "transition_with_event"
)


def _event(*, event_type: str = "experiment.transitioned") -> StoredEvent:
    return StoredEvent(
        id=17,
        project_id="proj_1",
        type=event_type,
        target_type="experiment",
        target_id="exp_1",
        payload=freeze_json_object({"transition": "start_running"}),
        created_at="2026-07-19T12:00:00Z",
    )


def _catalog_entry(
    *,
    event_type: str = "experiment.transitioned",
    phase: str = "post_commit",
    handler: str = "probe",
    failure: str = "fatal",
    idempotency: str = "repeat_safe",
) -> EventCatalogEntry:
    return EventCatalogEntry(
        producer=_TRANSITION_PRODUCER,
        event_type=event_type,
        payload_version=1,
        transaction_boundary=_TRANSITION_PRODUCER,
        reaction_phase=phase,
        handler_identity=handler,
        failure=failure,  # type: ignore[arg-type]
        idempotency=idempotency,  # type: ignore[arg-type]
    )


def _bind(dispatcher: EventDispatcher, *reactions) -> None:
    entries = tuple(
        _catalog_entry(
            event_type=event_type,
            phase=phase,
            handler=name,
            failure=failure,
        )
        for event_type, phase, name, _handler, failure in reactions
    )
    dispatcher.bind_catalog(
        entries,
        handlers={
            name: handler
            for _event, _phase, name, handler, _failure in reactions
        },
    )


class EventDispatcherTest(unittest.TestCase):
    def test_catalog_binding_records_the_exact_executable_registration(self) -> None:
        dispatcher = EventDispatcher()
        entry = _catalog_entry()

        dispatcher.bind_catalog(
            (entry,),
            handlers={"probe": lambda context: EventReaction(state=context.state)},
        )

        self.assertEqual(dispatcher.catalog, (entry,))
        state = {"status": "running"}
        self.assertIs(
            dispatcher.dispatch(
                event=_event(), phase="post_commit", state=state
            ).state,
            state,
        )

    def test_catalog_handler_mismatch_fails_before_partial_registration(self) -> None:
        dispatcher = EventDispatcher()
        entry = _catalog_entry()

        for handlers in (
            {},
            {"probe": lambda context: context, "stale": lambda context: context},
        ):
            with self.subTest(handlers=tuple(handlers)):
                with self.assertRaisesRegex(ValueError, "handler mismatch"):
                    dispatcher.bind_catalog((entry,), handlers=handlers)
                self.assertEqual(dispatcher.catalog, ())

    def test_advisory_catalog_entries_must_declare_repeat_safety(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid event catalog entry"):
            _catalog_entry(
                failure="advisory",
                idempotency="requires_adapter_key_for_redelivery",
            )

    def test_catalog_identity_fields_are_required(self) -> None:
        for field in (
            "producer",
            "event_type",
            "transaction_boundary",
            "reaction_phase",
            "handler_identity",
        ):
            with self.subTest(field=field):
                values: dict[str, object] = {
                    "producer": _TRANSITION_PRODUCER,
                    "event_type": "experiment.transitioned",
                    "payload_version": 1,
                    "transaction_boundary": _TRANSITION_PRODUCER,
                    "reaction_phase": "post_commit",
                    "handler_identity": "probe",
                    "failure": "fatal",
                    "idempotency": "repeat_safe",
                }
                values[field] = " "
                with self.assertRaisesRegex(ValueError, "invalid event catalog entry"):
                    EventCatalogEntry(**values)  # type: ignore[arg-type]

    def test_selected_phase_runs_in_registration_order_and_threads_exact_state(self) -> None:
        event = _event()
        initial = {"step": 0}
        after_first = {"step": 1}
        after_second = {"step": 2}
        seen: list[tuple[str, object, object]] = []
        dispatcher = EventDispatcher()

        def first(context):
            seen.append(("first", context.event, context.state))
            return EventReaction(state=after_first, value={"run_id": "run_1"})

        def second(context):
            seen.append(("second", context.event, context.state))
            return EventReaction(state=after_second)

        def other_phase(context):  # pragma: no cover - must not be selected
            raise AssertionError(f"unexpected phase for {context.event.type}")

        _bind(
            dispatcher,
            (event.type, "post_commit", "first", first, "fatal"),
            (event.type, "post_commit", "second", second, "fatal"),
            (event.type, "post_response", "other", other_phase, "fatal"),
        )

        result = dispatcher.dispatch(event=event, phase="post_commit", state=initial)

        self.assertIs(result.state, after_second)
        self.assertEqual(dict(result.outcomes), {"first": {"run_id": "run_1"}})
        self.assertEqual(
            seen,
            [("first", event, initial), ("second", event, after_first)],
        )

    def test_unknown_event_or_phase_is_exact_no_op(self) -> None:
        dispatcher = EventDispatcher()
        state = {"status": "running"}
        event = _event(event_type="unregistered")

        for phase in ("post_commit", "unknown"):
            result = dispatcher.dispatch(event=event, phase=phase, state=state)
            self.assertIs(result.state, state)
            self.assertEqual(dict(result.outcomes), {})

    def test_duplicate_name_for_event_phase_is_rejected(self) -> None:
        dispatcher = EventDispatcher()
        handler = lambda context: EventReaction(state=context.state)
        reaction = (
            "experiment.transitioned",
            "post_commit",
            "tracking",
            handler,
            "fatal",
        )

        with self.assertRaisesRegex(
            ValueError, "duplicate event catalog registration"
        ):
            _bind(dispatcher, reaction, reaction)
        self.assertEqual(dispatcher.catalog, ())

    def test_catalog_can_only_be_bound_once(self) -> None:
        dispatcher = EventDispatcher()
        reaction = (
            "experiment.transitioned",
            "post_commit",
            "tracking",
            lambda context: EventReaction(state=context.state),
            "fatal",
        )
        _bind(dispatcher, reaction)

        with self.assertRaisesRegex(ValueError, "already bound"):
            _bind(dispatcher, reaction)

    def test_uncaught_error_propagates_and_stops_phase(self) -> None:
        dispatcher = EventDispatcher()
        calls: list[str] = []
        threaded = {"status": "running"}

        def first(context):
            calls.append("first")
            return EventReaction(state=threaded)

        def explode(context):
            calls.append("explode")
            self.assertIs(context.state, threaded)
            raise RuntimeError("reaction failed")

        def never(context):  # pragma: no cover - propagation must stop first
            calls.append("never")
            return EventReaction(state=context.state)

        _bind(
            dispatcher,
            *(
                ("experiment.transitioned", "post_commit", name, handler, "fatal")
                for name, handler in (
                    ("first", first),
                    ("explode", explode),
                    ("never", never),
                )
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "reaction failed"):
            dispatcher.dispatch(
                event=_event(), phase="post_commit", state={"status": "ready_to_run"}
            )
        self.assertEqual(calls, ["first", "explode"])

    def test_advisory_error_is_dropped_and_later_handler_runs(self) -> None:
        dispatcher = EventDispatcher()
        calls: list[str] = []

        def advisory(_context):
            calls.append("advisory")
            raise RuntimeError("optional integration unavailable")

        def final(context):
            calls.append("final")
            return EventReaction(state=context.state, value="kept")

        _bind(
            dispatcher,
            (
                "experiment.transitioned",
                "post_response",
                "advisory",
                advisory,
                "advisory",
            ),
            ("experiment.transitioned", "post_response", "final", final, "fatal"),
        )

        result = dispatcher.dispatch(
            event=_event(), phase="post_response", state={"status": "complete"}
        )

        self.assertEqual(calls, ["advisory", "final"])
        self.assertEqual(dict(result.outcomes), {"final": "kept"})

    def test_unknown_failure_mode_is_rejected_at_composition(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid event catalog entry"):
            _catalog_entry(
                handler="bad",
                failure="ignored",  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()

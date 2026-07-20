from __future__ import annotations

import unittest

from merv.brain.application.events import EventDispatcher, EventReaction
from merv.brain.kernel.events import StoredEvent, freeze_json_object


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


class EventDispatcherTest(unittest.TestCase):
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

        dispatcher.register(
            event_type=event.type, phase="post_commit", name="first", handler=first
        )
        dispatcher.register(
            event_type=event.type, phase="post_commit", name="second", handler=second
        )
        dispatcher.register(
            event_type=event.type,
            phase="post_response",
            name="other",
            handler=other_phase,
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
        dispatcher.register(
            event_type="experiment.transitioned",
            phase="post_commit",
            name="tracking",
            handler=handler,
        )

        with self.assertRaisesRegex(ValueError, "duplicate event handler"):
            dispatcher.register(
                event_type="experiment.transitioned",
                phase="post_commit",
                name="tracking",
                handler=handler,
            )

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

        for name, handler in (("first", first), ("explode", explode), ("never", never)):
            dispatcher.register(
                event_type="experiment.transitioned",
                phase="post_commit",
                name=name,
                handler=handler,
            )

        with self.assertRaisesRegex(RuntimeError, "reaction failed"):
            dispatcher.dispatch(
                event=_event(), phase="post_commit", state={"status": "ready_to_run"}
            )
        self.assertEqual(calls, ["first", "explode"])


if __name__ == "__main__":
    unittest.main()

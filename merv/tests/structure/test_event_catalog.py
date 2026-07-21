from __future__ import annotations

import inspect
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import Mock

from merv.brain.application.events import EventDispatcher
from merv.brain.application.experiments.reactions import (
    EXPERIMENT_REACTION_CATALOG,
    ExperimentReactions,
)


APPLICATION = Path(__file__).resolve().parents[2] / "src/merv/brain/application"
RESEARCH = APPLICATION.parent / "research_core"


def _resolve(identity: str):
    module_name, owner_name, member_name = identity.rsplit(".", 2)
    owner = getattr(import_module(module_name), owner_name)
    return getattr(owner, member_name)


class EventCatalogStructureTest(unittest.TestCase):
    def test_catalog_is_the_complete_runtime_registration_source(self) -> None:
        registry = EventDispatcher()
        ExperimentReactions(
            research=Mock(), feed=Mock(), tracking=None
        ).bind(registry)

        self.assertEqual(registry.catalog, EXPERIMENT_REACTION_CATALOG)
        self.assertEqual(
            tuple(
                (
                    entry.event_type,
                    entry.payload_version,
                    entry.reaction_phase,
                    entry.handler_identity,
                    entry.failure,
                    entry.idempotency,
                )
                for entry in EXPERIMENT_REACTION_CATALOG
            ),
            (
                (
                    "experiment.transitioned",
                    1,
                    "post_commit",
                    "tracking_start",
                    "fatal",
                    "requires_adapter_key_for_redelivery",
                ),
                (
                    "experiment.transitioned",
                    1,
                    "post_commit",
                    "tracking_finalize",
                    "advisory",
                    "repeat_safe",
                ),
                (
                    "experiment.transitioned",
                    1,
                    "post_response",
                    "feed",
                    "advisory",
                    "repeat_safe",
                ),
                (
                    "review.submitted",
                    1,
                    "producer_read",
                    "feed",
                    "advisory",
                    "repeat_safe",
                ),
                (
                    "experiment.mlflow_run_refreshed",
                    1,
                    "post_response",
                    "feed",
                    "advisory",
                    "repeat_safe",
                ),
            ),
        )

    def test_catalog_producers_and_atomic_boundaries_are_live(self) -> None:
        research_source = "\n".join(
            path.read_text(encoding="utf-8") for path in RESEARCH.rglob("*.py")
        )
        for entry in EXPERIMENT_REACTION_CATALOG:
            with self.subTest(
                entry=(entry.event_type, entry.reaction_phase, entry.handler_identity)
            ):
                producer = _resolve(entry.producer)
                boundary = _resolve(entry.transaction_boundary)
                self.assertEqual(entry.transaction_boundary, entry.producer)
                self.assertIn("self.store.record_event", inspect.getsource(producer))
                self.assertIn(entry.event_type, research_source)
                self.assertIn("with self.store.transaction()", inspect.getsource(boundary))

    def test_application_reactions_cannot_bypass_the_catalog(self) -> None:
        self.assertFalse(hasattr(EventDispatcher, "register"))
        offenders = []
        for path in APPLICATION.rglob("*.py"):
            relative = path.relative_to(APPLICATION).as_posix()
            if path.name == "events.py":
                continue
            source = path.read_text(encoding="utf-8")
            if ".register(" in source or (
                ".bind_catalog(" in source and relative != "experiments/reactions.py"
            ):
                offenders.append(relative)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

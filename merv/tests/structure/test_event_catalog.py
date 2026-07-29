from __future__ import annotations

import ast
import re
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
EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
FROZEN_DURABLE_EVENT_TYPES = frozenset(
    {
        "artifact.pinned",
        "artifact.submitted",
        "claim.created",
        "claim.updated",
        "experiment.created",
        "experiment.exhibit_generated",
        "experiment.mlflow_run_created",
        "experiment.mlflow_run_refreshed",
        "experiment.mlflow_run_unavailable",
        "experiment.returned_to_planned",
        "experiment.returned_to_running",
        "experiment.transitioned",
        "feed.author_registered",
        "feed.post_created",
        "litreview.paper_cited",
        "litreview.section_added",
        "litreview.section_deleted",
        "litreview.section_edited",
        "litreview.sections_reordered",
        "project.created",
        "project.updated",
        "reflection.created",
        "reflection.returned_to_reflecting",
        "reflection.returned_to_synthesizing",
        "reflection.transitioned",
        "review.requested",
        "review.started",
        "review.submitted",
        "run.finished",
        "sandbox.attached",
        "sandbox.cleanup_confirmed",
        "sandbox.cleanup_pending",
        "sandbox.cleanup_retried",
        "sandbox.created",
        "sandbox.endpoint_refreshed",
        "sandbox.expired",
        "sandbox.failed",
        "sandbox.idle_reaped",
        "sandbox.lifetime_extended",
        "sandbox.released",
        "sandbox.reused",
        "storage.completed",
        "storage.deleted",
        "storage.expired",
        "storage.registered",
        "telemetry.dropped",
        "tool.call",
    }
)


def _resolve(identity: str):
    module_name, owner_name, member_name = identity.rsplit(".", 2)
    owner = getattr(import_module(module_name), owner_name)
    return getattr(owner, member_name)


class EventCatalogStructureTest(unittest.TestCase):
    def test_complete_durable_event_name_inventory_is_frozen(self) -> None:
        """Catch coordinated producer/consumer renames outside the reaction catalog."""
        found: set[str] = set()
        for path in APPLICATION.parent.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                expressions = [
                    keyword.value
                    for keyword in call.keywords
                    if keyword.arg in {"event", "event_type"}
                ]
                if (
                    isinstance(call.func, ast.Name)
                    and call.func.id == "LifecycleEvent"
                    and call.args
                ):
                    expressions.append(call.args[0])
                for expression in expressions:
                    found.update(
                        str(node.value)
                        for node in ast.walk(expression)
                        if isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and EVENT_TYPE.fullmatch(node.value)
                    )
        self.assertEqual(found, FROZEN_DURABLE_EVENT_TYPES)

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
                    "degraded",
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

    def test_catalog_operation_identities_are_live(self) -> None:
        for entry in EXPERIMENT_REACTION_CATALOG:
            with self.subTest(
                entry=(entry.event_type, entry.reaction_phase, entry.handler_identity)
            ):
                self.assertTrue(callable(_resolve(entry.producer)))
                self.assertTrue(callable(_resolve(entry.transaction_boundary)))

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

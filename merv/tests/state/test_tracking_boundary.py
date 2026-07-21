from __future__ import annotations

import unittest
from typing import get_type_hints

import merv.brain.mlflow as mlflow_adapter
import merv.brain.mlflow.tracking as mlflow_tracking
from merv.brain.application.experiments.exhibits import ExperimentExhibits
from merv.brain.application.ports.tracking import (
    CreateRunResult,
    ExperimentTracking,
    FinalizeRunResult,
    MetricsSnapshot,
    TRACKING_NAMESPACE_PREFIX,
    TRACKING_TERMINAL_RUN_STATUSES,
    TRACKING_CAPABILITY_TRUTH_TABLE,
    TrackingCapabilities,
    TrackingContext,
    TrackingContextPayload,
    tracking_experiment_name,
)
from merv.brain.mlflow.tracking import CentralMlflowService


class TrackingBoundaryTest(unittest.TestCase):
    def test_central_mlflow_implements_the_narrow_tracking_port(self) -> None:
        service = CentralMlflowService(
            tracking_uri="https://agent.example.test",
            server_uri="http://control:5000",
        )

        self.assertIsInstance(service, ExperimentTracking)
        self.assertIsInstance(
            service.context(project_id="proj", experiment_id="exp"),
            TrackingContext,
        )
        self.assertIs(
            get_type_hints(CentralMlflowService.create_run)["return"],
            CreateRunResult,
        )
        self.assertIs(
            get_type_hints(CentralMlflowService.finalize_run)["return"],
            FinalizeRunResult,
        )
        self.assertIs(
            get_type_hints(CentralMlflowService.project_context)["return"],
            TrackingContextPayload,
        )
        self.assertIs(
            get_type_hints(CentralMlflowService.results_metrics)["return"],
            MetricsSnapshot,
        )

    def test_capabilities_follow_the_four_configuration_modes(self) -> None:
        cases = (
            (
                {},
                TrackingCapabilities(logging=False, control=False, readback=False),
            ),
            (
                {"tracking_uri": "https://agent.example.test"},
                TrackingCapabilities(logging=True, control=False, readback=True),
            ),
            (
                {"server_uri": "http://control:5000"},
                TrackingCapabilities(logging=False, control=True, readback=True),
            ),
            (
                {
                    "tracking_uri": "https://agent.example.test",
                    "server_uri": "http://control:5000",
                },
                TrackingCapabilities(logging=True, control=True, readback=True),
            ),
        )

        for configuration, expected in cases:
            with self.subTest(configuration=configuration):
                service = CentralMlflowService(**configuration)
                self.assertEqual(service.capabilities(), expected)
                self.assertEqual(
                    service.capabilities(),
                    TRACKING_CAPABILITY_TRUTH_TABLE[
                        (expected.logging, expected.control)
                    ],
                )

    def test_exhibit_readback_uses_capabilities_without_adapter_uris(self) -> None:
        class TrackingWithoutPublicUris:
            def __init__(self) -> None:
                self.reads = 0

            def capabilities(self) -> TrackingCapabilities:
                return TrackingCapabilities(
                    logging=False, control=True, readback=True
                )

            def results_metrics(
                self, *, project_id: str, experiment_id: str
            ) -> MetricsSnapshot:
                self.reads += 1
                return {
                    "available": True,
                    "source": "mlflow",
                    "experiment_id": experiment_id,
                    "experiments": [
                        {
                            "experiment_id": "1",
                            "name": f"merv/{project_id}/{experiment_id}",
                            "runs": [],
                        }
                    ],
                }

        class Experiments:
            @staticmethod
            def attempt_started_running_at(*, experiment_id: str) -> str:
                return "2026-01-01T00:00:00Z"

        class Resources:
            @staticmethod
            def metric_file_sources(
                *, experiment_id: str, attempt_index: int
            ) -> list[dict[str, object]]:
                return []

        tracking = TrackingWithoutPublicUris()
        exhibit = ExperimentExhibits(
            research=Experiments(),  # type: ignore[arg-type]
            artifacts=Resources(),  # type: ignore[arg-type]
            tracking=tracking,  # type: ignore[arg-type]
        ).generate(
            state={
                "project_id": "proj",
                "id": "exp",
                "attempt_index": 2,
            },
        )

        self.assertEqual(tracking.reads, 1)
        self.assertTrue(exhibit["mlflow"]["configured"])
        self.assertTrue(exhibit["mlflow"]["available"])

    def test_tracking_contract_owns_shared_namespace_and_status_vocabulary(self) -> None:
        self.assertEqual(TRACKING_NAMESPACE_PREFIX, "merv")
        self.assertEqual(
            tracking_experiment_name(project_id="proj", experiment_id="exp"),
            "merv/proj/exp",
        )
        self.assertEqual(
            TRACKING_TERMINAL_RUN_STATUSES,
            frozenset({"FINISHED", "FAILED", "KILLED"}),
        )

    def test_mlflow_package_exports_only_concrete_adapter_entrypoints(self) -> None:
        self.assertEqual(
            mlflow_adapter.__all__,
            ["CentralMlflowService", "LocalMlflowServer"],
        )
        for compatibility_name in (
            "build_metrics_exhibit",
            "exhibit_bytes",
            "mlflow_experiment_name",
            "mlflow_visible_for_status",
            "MLFLOW_NAMESPACE_PREFIX",
            "MLFLOW_TERMINAL_RUN_STATUSES",
            "MLFLOW_STATE_STATUSES",
            "tracking_experiment_name",
            "TRACKING_NAMESPACE_PREFIX",
            "TRACKING_TERMINAL_RUN_STATUSES",
        ):
            with self.subTest(name=compatibility_name):
                self.assertFalse(hasattr(mlflow_adapter, compatibility_name))
                self.assertFalse(hasattr(mlflow_tracking, compatibility_name))


if __name__ == "__main__":
    unittest.main()

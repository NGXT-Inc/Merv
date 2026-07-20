from __future__ import annotations

import unittest
from typing import get_type_hints

import merv.brain.mlflow as legacy_package
import merv.brain.mlflow.exhibit as legacy_exhibit
import merv.brain.mlflow.tracking as legacy_tracking
from merv.brain.application.experiments import metrics_exhibit, tracking_policy
from merv.brain.application.ports.tracking import (
    CreateRunResult,
    ExperimentTracking,
    FinalizeRunResult,
    MetricsSnapshot,
    TRACKING_CAPABILITY_TRUTH_TABLE,
    TrackingCapabilities,
    TrackingContext,
)
from merv.brain.mlflow.tracking import CentralMlflowService
from merv.brain.surface.tools.exhibits import generate_metrics_exhibit


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
                *, target_id: str, attempt_index: int
            ) -> list[dict[str, object]]:
                return []

        tracking = TrackingWithoutPublicUris()
        exhibit = generate_metrics_exhibit(
            experiments=Experiments(),
            resources=Resources(),
            mlflow_tracking=tracking,
            state={
                "project_id": "proj",
                "id": "exp",
                "attempt_index": 2,
            },
        )

        self.assertEqual(tracking.reads, 1)
        self.assertTrue(exhibit["mlflow"]["configured"])
        self.assertTrue(exhibit["mlflow"]["available"])

    def test_legacy_exports_are_the_application_policy_objects(self) -> None:
        self.assertIs(
            legacy_exhibit.build_metrics_exhibit,
            metrics_exhibit.build_metrics_exhibit,
        )
        self.assertIs(legacy_package.build_metrics_exhibit, metrics_exhibit.build_metrics_exhibit)
        self.assertIs(legacy_exhibit.exhibit_bytes, metrics_exhibit.exhibit_bytes)
        self.assertIs(legacy_package.exhibit_bytes, metrics_exhibit.exhibit_bytes)
        self.assertIs(
            legacy_exhibit.iso_to_epoch_ms,
            metrics_exhibit.iso_to_epoch_ms,
        )

        self.assertIs(
            legacy_tracking.mlflow_experiment_name,
            tracking_policy.mlflow_experiment_name,
        )
        self.assertIs(
            legacy_package.mlflow_experiment_name,
            tracking_policy.mlflow_experiment_name,
        )
        self.assertIs(
            legacy_tracking.mlflow_visible_for_status,
            tracking_policy.mlflow_visible_for_status,
        )
        self.assertIs(
            legacy_package.mlflow_visible_for_status,
            tracking_policy.mlflow_visible_for_status,
        )
        self.assertIs(
            legacy_tracking.MLFLOW_TERMINAL_RUN_STATUSES,
            tracking_policy.MLFLOW_TERMINAL_RUN_STATUSES,
        )
        self.assertIs(
            legacy_tracking.MLFLOW_STATE_STATUSES,
            tracking_policy.MLFLOW_STATE_STATUSES,
        )


if __name__ == "__main__":
    unittest.main()

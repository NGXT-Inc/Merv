from __future__ import annotations

from dataclasses import replace
import unittest

from merv.brain.application.queries import (
    ComputeCostQuery,
    ExperimentFigureQuery,
    LogicGraphQuery,
    MlflowOverviewQuery,
    TenantCountersQuery,
)
from merv.brain.artifacts import Artifact


class RecordingQuery:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class RecordingTracking:
    def __init__(self, *, reachable: bool = True) -> None:
        self.reachable = reachable
        self.calls = []

    def health(self):
        self.calls.append(("health", {}))
        return {"configured": True, "reachable": self.reachable}

    def results_metrics(self, **kwargs):
        self.calls.append(("results_metrics", kwargs))
        return {
            "experiment_id": kwargs["experiment_id"],
            "available": True,
            "dashboard_experiment_url": "https://tracking.test/#/experiments/7",
        }

    def project_results_snapshot(self, **kwargs):
        self.calls.append(("project_results_snapshot", kwargs))
        project_id = kwargs["project_id"]
        experiment_ids = kwargs["experiment_ids"]
        return (
            {
                f"merv/{project_id}/{experiment_id}": {
                    "experiment_id": str(index),
                    "name": f"merv/{project_id}/{experiment_id}",
                    "runs": [{"run_id": f"run_{index}"}],
                }
                for index, experiment_id in enumerate(experiment_ids, start=7)
            },
            [
                {
                    "name": f"merv/{project_id}/{experiment_id}",
                    "experiment_id": str(index),
                    "dashboard_experiment_url": (
                        f"https://tracking.test/#/experiments/{index}"
                    ),
                }
                for index, experiment_id in enumerate(experiment_ids, start=7)
            ]
            + [{"name": f"merv/{project_id}/stray", "experiment_id": "8"}],
            "",
        )


class GraphResearch:
    def __init__(self) -> None:
        self.resolved = []

    def experiment_state(self, **_kwargs):
        return {
            "id": "exp_1",
            "status": "running",
            "attempt_index": 2,
            "artifacts": [
                {
                    "id": "res_old",
                    "path": "old.json",
                    "role": "graph",
                    "attempt_index": 1,
                    "submitted_order": 1,
                    "association_version_id": "ver_old",
                },
                {
                    "id": "res_new",
                    "path": "new.json",
                    "role": "graph",
                    "attempt_index": 2,
                    "submitted_order": 2,
                    "association_version_id": "ver_new",
                },
            ],
        }

    def reflection_state(self, **_kwargs):
        return {"id": "syn_1", "attempt_index": 1, "artifacts": []}

    def reflection_overview(self, **_kwargs):
        return {"reflections": [{"id": "syn_1"}]}

    def project_logic_graph_selection(self, **_kwargs):
        return {"reflection": None, "graph_artifact": None, "signal": "stale"}

    def resolve_graph_refs(self, **kwargs):
        self.resolved.append(kwargs)
        return {
            ref: {"type": "claim", "resolved": True}
            for ref in kwargs["refs"]
            if ref == "claim_1"
        }


class GraphArtifacts:
    def __init__(self) -> None:
        self.get_calls = []

    def _content(self, artifact_id: str) -> bytes | None:
        if artifact_id == "res_new":
            return (
                b'{"version":1,"nodes":['
                b'{"id":"n","label":"New","refs":["claim_1"]}]}'
            )
        return None

    def _reference(self, artifact_id: str) -> Artifact | None:
        return None

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        payloads = []
        for artifact_id in kwargs["artifact_ids"]:
            content = self._content(artifact_id)
            artifact = (
                _artifact(artifact_id, role="graph")
                if content is not None
                else self._reference(artifact_id)
            )
            if artifact is not None:
                payloads.append(replace(artifact, data=content))
        return tuple(payloads)


def _artifact(artifact_id: str, *, role: str) -> Artifact:
    return Artifact(
        id=artifact_id,
        project_id="proj_1",
        target_type="experiment",
        target_id="exp_1",
        role=role,
        attempt_index=2,
        lens_id="",
        path=f"{artifact_id}.json",
        title="",
        sha256=f"sha-{artifact_id}",
        size_bytes=1,
        content_type="application/json",
        status="complete",
        created_by="agent",
        created_at="2026-07-19T12:00:00Z",
        updated_at="2026-07-19T12:00:00Z",
        order=1,
    )


class ApplicationQueryTest(unittest.TestCase):
    def test_tenant_counters_join_kernel_and_sandbox_readers(self) -> None:
        events = RecordingQuery(4)
        generations = RecordingQuery(
            {"sandbox_generations": 2, "sandbox_hours": 3.5}
        )

        result = TenantCountersQuery(
            event_count=events,
            generation_counters=generations,
        )(tenant_id="tenant_1")

        self.assertEqual(
            result,
            {
                "tenant_id": "tenant_1",
                "tool_calls": 4,
                "sandbox_generations": 2,
                "sandbox_hours": 3.5,
            },
        )
        self.assertEqual(events.calls, [{"tenant_id": "tenant_1"}])
        self.assertEqual(generations.calls, [{"tenant_id": "tenant_1"}])

    def test_compute_cost_hydrates_experiment_names(self) -> None:
        spend = RecordingQuery(
            {
                "total_usd": 3.5,
                "by_experiment": [
                    {"experiment_id": "exp_1"},
                    {"experiment_id": "exp_missing"},
                ],
            }
        )
        experiments = RecordingQuery(
            [{"id": "exp_1", "name": "First"}, {"id": "exp_2", "name": "Second"}]
        )

        result = ComputeCostQuery(
            project_spend=spend, experiments=experiments
        )(project_id="proj_1")

        self.assertEqual(
            result["by_experiment"],
            [
                {"experiment_id": "exp_1", "experiment_name": "First"},
                {"experiment_id": "exp_missing", "experiment_name": ""},
            ],
        )

    def test_logic_graph_query_owns_selection_parsing_lint_and_ref_resolution(self) -> None:
        research = GraphResearch()
        artifacts = GraphArtifacts()
        query = LogicGraphQuery(research=research, artifacts=artifacts)

        result = query.experiment(project_id="proj_1", experiment_id="exp_1")

        self.assertTrue(result["available"])
        self.assertEqual(result["artifact_id"], "res_new")
        self.assertEqual(result["graph"]["nodes"][0]["label"], "New")
        self.assertEqual(result["problems"], [])
        self.assertEqual(
            result["ref_index"],
            {"claim_1": {"type": "claim", "resolved": True}},
        )
        self.assertEqual(
            research.resolved,
            [{"project_id": "proj_1", "refs": ("claim_1",)}],
        )
        self.assertEqual(
            [call["artifact_ids"] for call in artifacts.get_calls],
            [("res_new",)],
        )

    def test_logic_graph_query_composes_refs_in_first_seen_order(self) -> None:
        class MixedResearch(GraphResearch):
            def resolve_graph_refs(self, **kwargs):
                self.resolved.append(kwargs)
                return {
                    "claim_1": {"type": "claim", "resolved": True},
                    "exp_missing": {"type": "unknown", "resolved": False},
                }

        class MixedArtifacts(GraphArtifacts):
            def _content(self, artifact_id: str) -> bytes | None:
                if artifact_id == "res_new":
                    return (
                        b'{"version":1,"nodes":['
                        b'{"id":"a","label":"A","refs":'
                        b'["claim_1","art_results","claim_1","exp_missing"]},'
                        b'{"id":"b","label":"B","refs":'
                        b'["art_missing","ghost.json"," ",7]}]}'
                    )
                return None

            def _reference(self, artifact_id: str) -> Artifact | None:
                if artifact_id == "art_results":
                    return _artifact(artifact_id, role="result")
                return None

        research = MixedResearch()
        artifacts = MixedArtifacts()

        result = LogicGraphQuery(research=research, artifacts=artifacts).experiment(
            project_id="proj_1", experiment_id="exp_1"
        )

        self.assertEqual(
            list(result["ref_index"]),
            [
                "claim_1",
                "art_results",
                "exp_missing",
                "art_missing",
                "ghost.json",
            ],
        )
        self.assertEqual(
            result["ref_index"]["exp_missing"],
            {"type": "unknown", "resolved": False},
        )
        self.assertEqual(result["ref_index"]["art_results"]["type"], "artifact")
        self.assertFalse(result["ref_index"]["art_missing"]["resolved"])
        self.assertIn(
            "not a submitted artifact id",
            result["ref_index"]["ghost.json"]["hint"],
        )
        self.assertEqual(
            research.resolved,
            [
                {
                    "project_id": "proj_1",
                    "refs": (
                        "claim_1",
                        "art_results",
                        "exp_missing",
                        "art_missing",
                        "ghost.json",
                    ),
                }
            ],
        )
        self.assertEqual(
            [call["artifact_ids"] for call in artifacts.get_calls],
            [("res_new",), ("art_results", "art_missing")],
        )

    def test_project_graph_keeps_signal_when_no_reflection_exists(self) -> None:
        result = LogicGraphQuery(
            research=GraphResearch(), artifacts=GraphArtifacts()
        ).project(project_id="proj_1")

        self.assertEqual(
            result,
            {
                "max_nodes": 16,
                "signal": "stale",
                "available": False,
                "reflection": None,
                "graph": None,
                "problems": [],
            },
        )

    def test_project_graph_presents_semantic_reflection_signal(self) -> None:
        class SemanticSignalResearch(GraphResearch):
            def project_logic_graph_selection(self, **_kwargs):
                return {
                    "reflection": None,
                    "graph_resource": None,
                    "signal": {
                        "terminal_experiments": 3,
                        "covered_terminal_experiments": 0,
                        "new_terminal_since_publish": 3,
                        "claims_changed_since_publish": 0,
                        "contradicted_flip": False,
                        "last_published_reflection_id": None,
                        "stale": True,
                        "experiment_create_blocked": False,
                    },
                }

        result = LogicGraphQuery(
            research=SemanticSignalResearch(), artifacts=GraphArtifacts()
        ).project(project_id="proj_1")

        self.assertEqual(list(result["signal"])[-1], "hint")
        self.assertIn("first reflection", result["signal"]["hint"])

    def test_mlflow_overview_preserves_mapping_and_history_policy(self) -> None:
        tracking = RecordingTracking()
        query = MlflowOverviewQuery(
            experiments=RecordingQuery(
                [
                    {
                        "id": "exp_1",
                        "name": "Experiment One",
                        "status": "running",
                        "intent": "Measure it",
                    }
                ]
            ),
            tracking=tracking,
        )

        result = query(project_id="proj_1")

        self.assertEqual(result["experiments"][0]["mlflow_experiment_name"], "merv/proj_1/exp_1")
        self.assertEqual(
            result["experiments"][0]["dashboard_experiment_url"],
            "https://tracking.test/#/experiments/7",
        )
        self.assertEqual(
            result["unmapped_mlflow_experiments"],
            [{"name": "merv/proj_1/stray", "experiment_id": "8"}],
        )
        self.assertIn(
            (
                "project_results_snapshot",
                {
                    "project_id": "proj_1",
                    "experiment_ids": ("exp_1",),
                },
            ),
            tracking.calls,
        )

    def test_mlflow_overview_uses_two_adapter_calls_for_one_and_twenty_five(self) -> None:
        for count in (1, 25):
            with self.subTest(count=count):
                tracking = RecordingTracking()
                summaries = [
                    {
                        "id": f"exp_{index:02d}",
                        "name": f"Experiment {index}",
                        "status": "running",
                        "intent": f"Measure {index}",
                    }
                    for index in range(count)
                ]

                result = MlflowOverviewQuery(
                    experiments=RecordingQuery(summaries), tracking=tracking
                )(project_id="proj_1")

                expected_ids = tuple(item["id"] for item in summaries)
                self.assertEqual(
                    tracking.calls,
                    [
                        ("health", {}),
                        (
                            "project_results_snapshot",
                            {
                                "project_id": "proj_1",
                                "experiment_ids": expected_ids,
                            },
                        ),
                    ],
                )
                self.assertEqual(
                    [item["experiment_id"] for item in result["experiments"]],
                    list(expected_ids),
                )
                self.assertEqual(
                    result["unmapped_mlflow_experiments"],
                    [{"name": "merv/proj_1/stray", "experiment_id": "8"}],
                )

    def test_mlflow_overview_short_circuits_an_unreachable_adapter(self) -> None:
        tracking = RecordingTracking(reachable=False)
        query = MlflowOverviewQuery(
            experiments=RecordingQuery([{"id": "exp_1"}]),
            tracking=tracking,
        )

        result = query(project_id="proj_1")

        self.assertEqual(
            result["experiments"][0]["metrics"],
            {
                "experiment_id": "exp_1",
                "available": False,
                "source": "mlflow",
                "hint": "MLflow unreachable.",
            },
        )
        self.assertEqual(result["unmapped_mlflow_experiments"], [])
        self.assertEqual(tracking.calls, [("health", {})])

    def test_mlflow_overview_distinguishes_batch_failure_from_no_runs(self) -> None:
        class SparseTracking(RecordingTracking):
            def __init__(self, hint):
                super().__init__()
                self.hint = hint

            def project_results_snapshot(self, **kwargs):
                self.calls.append(("project_results_snapshot", kwargs))
                return (
                    {},
                    [
                        {
                            "name": "merv/proj_1/exp_1",
                            "experiment_id": "7",
                            "dashboard_experiment_url": (
                                "https://tracking.test/#/experiments/7"
                            ),
                        },
                        {"name": "merv/proj_1/stray", "experiment_id": "8"},
                    ],
                    self.hint,
                )

        for failure_hint, expected_hint in (
            ("", "No MLflow runs found for this experiment yet."),
            ("MLflow unreachable.", "MLflow unreachable."),
        ):
            with self.subTest(failure_hint=failure_hint):
                result = MlflowOverviewQuery(
                    experiments=RecordingQuery([{"id": "exp_1"}]),
                    tracking=SparseTracking(failure_hint),
                )(project_id="proj_1")

                self.assertEqual(
                    result["experiments"][0]["metrics"]["hint"], expected_hint
                )
                self.assertEqual(
                    result["experiments"][0]["dashboard_experiment_url"], ""
                )
                self.assertNotIn(
                    "dashboard_experiment_url",
                    result["experiments"][0]["metrics"],
                )
                self.assertEqual(
                    result["unmapped_mlflow_experiments"],
                    [{"name": "merv/proj_1/stray", "experiment_id": "8"}],
                )

    def test_mlflow_overview_reads_namespace_for_an_empty_research_project(self) -> None:
        tracking = RecordingTracking()

        result = MlflowOverviewQuery(
            experiments=RecordingQuery([]), tracking=tracking
        )(project_id="proj_1")

        self.assertEqual(
            tracking.calls,
            [
                ("health", {}),
                (
                    "project_results_snapshot",
                    {"project_id": "proj_1", "experiment_ids": ()},
                ),
            ],
        )
        self.assertEqual(result["experiments"], [])
        self.assertEqual(
            result["unmapped_mlflow_experiments"],
            [{"name": "merv/proj_1/stray", "experiment_id": "8"}],
        )

    def test_figure_gathers_review_and_sandbox_facts_before_projection(self) -> None:
        experiment = {
            "id": "exp_1",
            "intent": "Test",
            "status": "running",
            "attempt_index": 2,
            "artifacts": [],
            "reviews": [{"id": "review_1", "target_snapshot_id": "snap_1", "verdict": "pass"}],
            "tested_claims": [],
        }
        state = RecordingQuery(experiment)
        snapshot = RecordingQuery({"attempt_index": 1})
        open_reviews = RecordingQuery([])
        sandbox_snapshot = RecordingQuery(
            ({"status": "running", "gpu": "H100"}, True)
        )
        query = ExperimentFigureQuery(
            experiment_state=state,
            review_snapshot=snapshot,
            open_reviews=open_reviews,
            sandbox_snapshot=sandbox_snapshot,
        )

        result = query(project_id="proj_1", experiment_id="exp_1")

        nodes = {node["id"]: node for node in result["nodes"]}
        self.assertEqual(nodes["review:review_1"]["group"], "attempt:1")
        self.assertEqual(nodes["sandbox"]["status"], "active")
        self.assertEqual(
            state.calls, [{"experiment_id": "exp_1", "project_id": "proj_1"}]
        )
        self.assertEqual(snapshot.calls, [{"snapshot_id": "snap_1"}])
        self.assertEqual(
            open_reviews.calls,
            [{"project_id": "proj_1", "experiment_id": "exp_1"}],
        )


if __name__ == "__main__":
    unittest.main()

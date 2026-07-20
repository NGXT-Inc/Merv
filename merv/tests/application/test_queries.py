from __future__ import annotations

import unittest

from merv.brain.application.queries import (
    ExperimentFigureQuery,
    HomeQuery,
    MlflowOverviewQuery,
)


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

    def namespace_experiments(self, **kwargs):
        self.calls.append(("namespace_experiments", kwargs))
        project_id = kwargs["project_id"]
        return [
            {"name": f"merv/{project_id}/exp_1", "experiment_id": "7"},
            {"name": f"merv/{project_id}/stray", "experiment_id": "8"},
        ]


class ApplicationQueryTest(unittest.TestCase):
    def test_home_assembles_the_exact_cross_component_read_model(self) -> None:
        project = {
            "id": "proj_1",
            "active_claims": [{"id": "claim_1"}],
            "active_experiments": [{"id": "exp_1"}],
        }
        experiment = {"id": "exp_1", "name": "Test", "status": "running"}
        active = {**experiment, "workflow": {"next_action": "retain_results"}}
        status = RecordingQuery({"project": project, "workflow": {"next_action": "fallback"}})
        experiments = RecordingQuery({"experiments": [experiment]})
        resources = RecordingQuery({"resources": [{"id": "res_1"}]})
        reviews = RecordingQuery({"requests": [{"id": "rr_1"}], "reviews": []})
        events = RecordingQuery({"events": [{"id": 9}]})
        work = RecordingQuery(
            {"active_experiments": [active], "active_processes": [{"id": "sbx_1"}]}
        )
        query = HomeQuery(
            experiments=experiments,
            resources=resources,
            status_and_next=status,
            active_work=work,
            review_queue=reviews,
            recent_events=events,
            health=lambda: {"configured": True},
        )

        result = query(project_id="proj_1")

        self.assertEqual(
            result,
            {
                "project": project,
                "claims": project["active_claims"],
                "experiments": [experiment],
                "active_experiments": [active],
                "active_processes": [{"id": "sbx_1"}],
                "resources": [{"id": "res_1"}],
                "reviews": reviews.result,
                "pending_change_sets": [],
                "recent_events": [{"id": 9}],
                "stats": {
                    "claims": 1,
                    "experiments": 1,
                    "active_experiments": 1,
                    "active_processes": 1,
                    "resources": 1,
                    "open_reviews": 1,
                },
                "workflow": active["workflow"],
                "active_experiment": active,
                "mlflow": {"configured": True},
            },
        )
        self.assertEqual(status.calls, [{"project_id": "proj_1"}])
        self.assertEqual(experiments.calls, [{"project_id": "proj_1"}])
        self.assertEqual(events.calls, [{"project_id": "proj_1", "limit": 25}])

    def test_mlflow_overview_preserves_mapping_and_history_policy(self) -> None:
        tracking = RecordingTracking()
        query = MlflowOverviewQuery(
            experiments=RecordingQuery(
                {
                    "experiments": [
                        {
                            "id": "exp_1",
                            "name": "Experiment One",
                            "status": "running",
                            "intent": "Measure it",
                        }
                    ]
                }
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
                "results_metrics",
                {
                    "project_id": "proj_1",
                    "experiment_id": "exp_1",
                    "include_history": False,
                },
            ),
            tracking.calls,
        )

    def test_mlflow_overview_short_circuits_an_unreachable_adapter(self) -> None:
        tracking = RecordingTracking(reachable=False)
        query = MlflowOverviewQuery(
            experiments=RecordingQuery({"experiments": [{"id": "exp_1"}]}),
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

    def test_figure_gathers_review_and_sandbox_facts_before_projection(self) -> None:
        experiment = {
            "id": "exp_1",
            "intent": "Test",
            "status": "running",
            "attempt_index": 2,
            "resources": [],
            "reviews": [{"id": "review_1", "target_snapshot_id": "snap_1", "verdict": "pass"}],
            "tested_claims": [],
        }
        state = RecordingQuery(experiment)
        snapshot = RecordingQuery({"attempt_index": 1})
        open_reviews = RecordingQuery([])
        sandbox_row = RecordingQuery({"status": "running", "gpu": "H100"})
        sandbox_view = RecordingQuery({"status": "running", "gpu": "H100"})
        query = ExperimentFigureQuery(
            experiment_state=state,
            review_snapshot=snapshot,
            open_reviews=open_reviews,
            sandbox_row=sandbox_row,
            sandbox_view=sandbox_view,
            sandbox_status_active={"running"}.__contains__,
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

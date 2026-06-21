from __future__ import annotations

import unittest

from backend.sandbox_autosync import run_auto_sync_target


class SandboxAutoSyncTest(unittest.TestCase):
    def test_sync_target_passes_row_when_requested_and_runs_after_sync(self) -> None:
        calls: list[tuple[str, dict]] = []
        target = {
            "row": {"project_id": "proj_1", "experiment_id": "exp_1"},
            "session": {"lease_id": "lease_1"},
        }

        def sync_pull(**kwargs):
            calls.append(("sync", kwargs))
            return {"pulled": 1}

        def after_sync(**kwargs):
            calls.append(("after", kwargs))
            return {"metrics": {"loss": 0.1}}

        result, snapshot = run_auto_sync_target(
            target=target,
            sync_pull=sync_pull,
            sync_includes_row=True,
            after_sync=after_sync,
        )

        self.assertEqual(result, {"pulled": 1})
        self.assertEqual(snapshot, {"metrics": {"loss": 0.1}})
        self.assertEqual(calls[0][1]["row"], target["row"])
        self.assertEqual(calls[0][1]["session"], target["session"])
        self.assertTrue(calls[0][1]["skip_if_busy"])
        self.assertEqual(calls[1][1], {"row": target["row"]})

    def test_sync_target_skips_after_sync_when_busy(self) -> None:
        after_calls = 0

        def sync_pull(**_kwargs):
            return {"skipped": "busy"}

        def after_sync(**_kwargs):
            nonlocal after_calls
            after_calls += 1

        result, snapshot = run_auto_sync_target(
            target={"row": {"experiment_id": "exp_1"}, "session": {}},
            sync_pull=sync_pull,
            after_sync=after_sync,
        )

        self.assertEqual(result, {"skipped": "busy"})
        self.assertIsNone(snapshot)
        self.assertEqual(after_calls, 0)

    def test_after_sync_failure_is_best_effort(self) -> None:
        def sync_pull(**_kwargs):
            return {"pulled": 1}

        def after_sync(**_kwargs):
            raise RuntimeError("metrics unavailable")

        result, snapshot = run_auto_sync_target(
            target={"row": {"experiment_id": "exp_1"}, "session": {}},
            sync_pull=sync_pull,
            after_sync=after_sync,
        )

        self.assertEqual(result, {"pulled": 1})
        self.assertIsNone(snapshot)

    def test_missing_session_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "session is required"):
            run_auto_sync_target(target={"row": {}}, sync_pull=lambda **_: {})


class DaemonAutoSyncTest(unittest.TestCase):
    def test_daemon_auto_sync_reports_row_experiment_id(self) -> None:
        from backend.composition.daemon_mode import DaemonServer

        test_case = self

        class OneShotStop:
            def __init__(self) -> None:
                self.calls = 0

            def wait(self, _interval: float) -> bool:
                self.calls += 1
                return self.calls > 1

        class Worker:
            def __init__(self) -> None:
                self.sync_sessions: list[dict[str, object]] = []

            def sync_pull(self, **kwargs):
                self.sync_sessions.append(kwargs["session"])
                return {"pulled": 1}

            def capture_metrics_snapshot(self, **kwargs):
                test_case.assertEqual(kwargs["row"]["experiment_id"], "exp_from_row")
                return {"metrics": {"loss": 0.1}}

        class Control:
            def __init__(self) -> None:
                self.metrics: list[dict[str, object]] = []

            def submit_sandbox_metrics(self, payload: dict[str, object]) -> None:
                self.metrics.append(payload)

        class View:
            def sync_targets(self):
                return [
                    {
                        "experiment_id": "exp_from_stale_target",
                        "row": {
                            "project_id": "proj_1",
                            "experiment_id": "exp_from_row",
                        },
                        "session": {"lease_id": "lease_1"},
                    }
                ]

        worker = Worker()
        control = Control()
        server = DaemonServer(
            worker=worker,
            control=control,
            task_loop=object(),
            view=View(),
            project_links=object(),
            loopback_secret="secret",
            auto_sync_interval_seconds=0,
        )
        server._auto_sync_stop = OneShotStop()

        server._auto_sync_loop()

        self.assertEqual(worker.sync_sessions, [{"lease_id": "lease_1"}])
        self.assertEqual(
            control.metrics,
            [
                {
                    "project_id": "proj_1",
                    "experiment_id": "exp_from_row",
                    "metrics_snapshot": {"metrics": {"loss": 0.1}},
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()

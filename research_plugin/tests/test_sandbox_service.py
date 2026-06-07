from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.app import ResearchPluginApp
from backend.execution.backends.fake import FakeSandboxBackend
from backend.execution.types import SandboxRequest
from backend.utils import NotFoundError, PermissionDeniedError, ValidationError


class SandboxServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.backend = FakeSandboxBackend()
        self.app = ResearchPluginApp(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            execution_backend=self.backend,
        )
        self.project_id = self.call("project.create", name="Sandbox Project")["id"]

    def tearDown(self) -> None:
        self.app.shutdown()
        self.tmp.cleanup()

    def call(self, tool: str, **kwargs):
        return self.app.call_tool(tool, kwargs)

    def _experiment(self, *, status: str = "ready_to_run") -> str:
        exp_id = self.call("experiment.create", project_id=self.project_id, intent="x")["id"]
        if status != "planned":
            with self.app.store.transaction() as conn:
                conn.execute("UPDATE experiments SET status = ? WHERE id = ?", (status, exp_id))
        return exp_id

    # ---- gating ----

    def test_request_requires_ready_or_running(self) -> None:
        exp_id = self._experiment(status="planned")
        with self.assertRaises(PermissionDeniedError):
            self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)

    def test_request_unknown_experiment(self) -> None:
        with self.assertRaises(NotFoundError):
            self.call("sandbox.request", project_id=self.project_id, experiment_id="exp_nope")

    # ---- procurement ----

    def test_request_creates_and_returns_ssh(self) -> None:
        exp_id = self._experiment()
        result = self.call(
            "sandbox.request", project_id=self.project_id, experiment_id=exp_id, gpu="A100", time_limit=1200
        )
        self.assertEqual(result["status"], "running")
        self.assertFalse(result["reused"])
        self.assertTrue(result["sandbox_id"])
        # Short agent-facing command goes through the repo-local dispatcher.
        self.assertEqual(result["ssh"]["command"], f".research_plugin/sbx {exp_id}")
        self.assertEqual(result["sandbox_data_dir"], "/workspace/sandbox_data")
        # Full ssh line is still available as a cwd-independent fallback.
        self.assertTrue(result["ssh"]["raw_command"].startswith("ssh -i "))
        self.assertIn("@sandbox.modal.test", result["ssh"]["raw_command"])
        self.assertTrue(Path(result["ssh"]["key_path"]).exists())
        self.assertTrue(Path(result["ssh"]["key_path"] + ".pub").exists())
        # experiment flips to running
        state = self.call("experiment.get_state", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(state["status"], "running")

    def test_request_and_get_report_huggingface_env_without_secret_value(self) -> None:
        self.backend.sandbox_environment = lambda: {  # type: ignore[method-assign]
            "available_tokens": ["HF_TOKEN"],
            "notes": ["HF_TOKEN is available inside the sandbox."],
        }
        exp_id = self._experiment()
        result = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(result["environment"]["available_tokens"], ["HF_TOKEN"])
        self.assertIn("Hugging Face", result["hint"])
        self.assertIn("HF_TOKEN", result["hint"])
        self.assertNotIn("hf_", str(result))

        got = self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(got["environment"]["available_tokens"], ["HF_TOKEN"])
        self.assertIn("HF_TOKEN", got["hint"])

    def test_request_writes_dispatcher_and_conn(self) -> None:
        exp_id = self._experiment()
        self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        dispatcher = self.repo / ".research_plugin" / "sbx"
        conn = self.repo / ".research_plugin" / "sandboxes" / "conn" / exp_id
        self.assertTrue(dispatcher.exists())
        self.assertTrue(os.access(dispatcher, os.X_OK))
        self.assertTrue(conn.exists())
        body = conn.read_text()
        self.assertIn("RP_SSH_HOST=", body)
        self.assertIn("RP_SSH_PORT=", body)
        # Releasing the sandbox drops the conn file so `sbx` fails loudly.
        self.call("sandbox.release", project_id=self.project_id, experiment_id=exp_id)
        self.assertFalse(conn.exists())

    def test_request_reuses_live_sandbox(self) -> None:
        exp_id = self._experiment()
        first = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        second = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.assertTrue(second["reused"])
        self.assertEqual(first["sandbox_id"], second["sandbox_id"])
        self.assertEqual(len(self.backend.acquired), 1)

    def test_request_recreates_after_death(self) -> None:
        exp_id = self._experiment()
        first = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.backend.kill(sandbox_id=first["sandbox_id"])
        second = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.assertFalse(second["reused"])
        self.assertNotEqual(first["sandbox_id"], second["sandbox_id"])
        self.assertEqual(len(self.backend.acquired), 2)

    # ---- tunnel endpoint refresh (alive sandbox, moved tunnel) ----

    def test_get_refreshes_moved_endpoint(self) -> None:
        exp_id = self._experiment()
        created = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        old_host = created["ssh"]["host"]
        # Sandbox stays alive but Modal relocates its SSH tunnel.
        self.backend.move_endpoint(
            sandbox_id=created["sandbox_id"], host="r999.modal.host", port=55555
        )
        got = self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(got["status"], "running")
        self.assertNotEqual(got["ssh"]["host"], old_host)
        self.assertEqual(got["ssh"]["host"], "r999.modal.host")
        self.assertEqual(got["ssh"]["port"], 55555)
        # The conn file the dispatcher sources must carry the refreshed endpoint.
        body = (self.repo / ".research_plugin" / "sandboxes" / "conn" / exp_id).read_text()
        self.assertIn("r999.modal.host", body)
        self.assertIn("55555", body)

    # ---- observability dashboards (MLflow + TensorBoard) ----

    def test_request_surfaces_dashboard_urls(self) -> None:
        # The agent view carries the dashboard URLs so the (rare) agent that
        # wants to show them in transcripts can; the user-facing UI view does
        # the same. URL strings come straight from the backend's encrypted
        # tunnel surface — no rewriting.
        exp_id = self._experiment()
        result = self.call(
            "sandbox.request", project_id=self.project_id, experiment_id=exp_id
        )
        self.assertIn("dashboards", result)
        self.assertIn("mlflow", result["dashboards"])
        self.assertTrue(result["dashboards"]["mlflow"].startswith("https://mlflow-"))
        self.assertIn("tensorboard", result["dashboards"])
        # And the hint nudges the agent toward the auto-detect path so HF
        # Trainer and Lightning users get charts for free.
        self.assertIn("MLFLOW_TRACKING_URI", result["hint"])
        self.assertIn("mlflow.autolog", result["hint"])

    def test_ui_view_exposes_dashboards(self) -> None:
        # The HTTP API surfaces dashboards in the sandbox row so the UI can
        # render an iframe tab per non-empty entry. Empty {} when the backend
        # exposes none — never a missing key.
        exp_id = self._experiment()
        created = self.call(
            "sandbox.request", project_id=self.project_id, experiment_id=exp_id
        )
        view = self.app.sandboxes.get_for_ui(
            project_id=self.project_id, experiment_id=exp_id
        )
        self.assertEqual(
            view["dashboards"],
            {
                "mlflow": f"https://mlflow-{created['sandbox_id']}.modal.test",
                "tensorboard": f"https://tensorboard-{created['sandbox_id']}.modal.test",
            },
        )

    def test_get_refreshes_moved_dashboards(self) -> None:
        # When Modal relocates a live sandbox's tunnels, the dashboard URLs
        # change alongside the SSH endpoint. Reconcile must persist the fresh
        # URLs so the UI iframe doesn't 404 on stale ones.
        exp_id = self._experiment()
        created = self.call(
            "sandbox.request", project_id=self.project_id, experiment_id=exp_id
        )
        relocated = {
            "mlflow": "https://mlflow-r999.modal.host",
            "tensorboard": "https://tb-r999.modal.host",
        }
        self.backend.move_dashboards(
            sandbox_id=created["sandbox_id"], urls=relocated
        )
        got = self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(got["dashboards"], relocated)

    def test_dashboards_empty_when_backend_exposes_none(self) -> None:
        # CPU-only / older backends may surface no dashboards. The field must
        # still be present (empty dict) so the UI keys defensively.
        exp_id = self._experiment()
        # Pre-empty the backend's default before acquire stores the row.
        original_acquire = self.backend.acquire

        def acquire_without_dashboards(*, request, on_phase=None, on_created=None):
            provisioned = original_acquire(
                request=request, on_phase=on_phase, on_created=on_created
            )
            self.backend.dashboards[provisioned.sandbox_id] = {}
            from dataclasses import replace
            return replace(provisioned, dashboards={})

        self.backend.acquire = acquire_without_dashboards  # type: ignore[method-assign]
        result = self.call(
            "sandbox.request", project_id=self.project_id, experiment_id=exp_id
        )
        self.assertEqual(result["dashboards"], {})
        view = self.app.sandboxes.get_for_ui(
            project_id=self.project_id, experiment_id=exp_id
        )
        self.assertEqual(view["dashboards"], {})

    # ---- status / liveness ----

    def test_get_reconciles_dead_sandbox(self) -> None:
        exp_id = self._experiment()
        created = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.backend.kill(sandbox_id=created["sandbox_id"])
        got = self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(got["status"], "terminated")

    def test_get_scoped_to_project(self) -> None:
        exp_id = self._experiment()
        self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        other = self.call("project.create", name="Other")["id"]
        with self.assertRaises(NotFoundError):
            self.call("sandbox.get", project_id=other, experiment_id=exp_id)

    # ---- live usage metrics ----

    def test_metrics_for_running_sandbox(self) -> None:
        exp_id = self._experiment()
        created = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        sample = {
            "cpu": {"used_cores": 1.5, "limit_cores": 2.0},
            "memory": {"used_bytes": 2147483648, "limit_bytes": 8589934592},
            "gpus": [{"index": 0, "name": "A100", "util_pct": 42, "mem_used_mib": 1024, "mem_total_mib": 40960}],
        }
        self.backend.metrics[created["sandbox_id"]] = sample
        result = self.app.sandboxes.metrics_for_ui(
            project_id=self.project_id, experiment_id=exp_id
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["metrics"], sample)
        # The row's reserved request rides along to frame the bars.
        self.assertEqual(result["reserved"]["cpu"], 2.0)

    # ---- terminal ----

    def test_terminal_reads_transcript(self) -> None:
        exp_id = self._experiment()
        self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.backend.append_transcript(experiment_id=exp_id, text="$ python train.py\nloss 0.1\n")
        term = self.call("sandbox.terminal", project_id=self.project_id, experiment_id=exp_id)
        self.assertIn("train.py", term["transcript"])

    def test_sync_commits_sandbox_and_returns_resource_guidance(self) -> None:
        exp_id = self._experiment()
        created = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        result = self.call("sandbox.sync", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["sync"]["sandbox_id"], created["sandbox_id"])
        self.assertTrue(result["sync"]["committed"])
        self.assertIn("resource.register_file", result["hint"])
        self.assertEqual(self.backend.synced[-1]["sandbox_id"], created["sandbox_id"])

    def test_sync_requires_running_sandbox(self) -> None:
        exp_id = self._experiment()
        with self.assertRaises(ValidationError):
            self.call("sandbox.sync", project_id=self.project_id, experiment_id=exp_id)

    # ---- release ----

    def test_release_terminates(self) -> None:
        exp_id = self._experiment()
        created = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        released = self.call("sandbox.release", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(released["status"], "terminated")
        self.assertIn(created["sandbox_id"], self.backend.terminated)

    # ---- list ----

    def test_list_returns_project_sandboxes(self) -> None:
        exp_id = self._experiment()
        self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        listed = self.call("sandbox.list", project_id=self.project_id)["sandboxes"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["experiment_id"], exp_id)

    # ---- validation ----

    def test_invalid_gpu_rejected(self) -> None:
        exp_id = self._experiment()
        with self.assertRaises(ValidationError):
            self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id, gpu="NOTREAL")

    def test_invalid_time_limit_rejected(self) -> None:
        exp_id = self._experiment()
        with self.assertRaises(ValidationError):
            self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id, time_limit=5)

    # ---- async provisioning ----

    def _await_status(self, exp_id: str, target: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            got = self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)
            if got["status"] == target:
                return got
            time.sleep(0.02)
        return self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)

    def test_request_returns_provisioning_when_slow(self) -> None:
        # Budget below the gated acquire so request falls back to provisioning.
        self.app.sandboxes.request_wait_seconds = 0.05
        self.backend.gate = threading.Event()
        exp_id = self._experiment()
        result = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(result["status"], "provisioning")
        self.assertEqual(result["poll_after_seconds"], 10)
        self.assertEqual(result["ssh"]["command"], "")
        # get keeps reporting provisioning while the job is gated.
        polled = self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(polled["status"], "provisioning")
        # Release the gate; the job finishes and get flips to running with SSH.
        self.backend.gate.set()
        final = self._await_status(exp_id, "running")
        self.assertEqual(final["status"], "running")
        self.assertEqual(final["ssh"]["command"], f".research_plugin/sbx {exp_id}")

    def test_provisioning_failure_marks_failed_and_cleans_up(self) -> None:
        self.app.sandboxes.request_wait_seconds = 2.0
        self.backend.fail_after_create = True
        exp_id = self._experiment()
        result = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["error"])
        # The sandbox that was created before the tunnel failure got terminated.
        self.assertTrue(self.backend.terminated)

    def test_release_cancels_provisioning(self) -> None:
        self.app.sandboxes.request_wait_seconds = 0.05
        self.backend.gate = threading.Event()
        exp_id = self._experiment()
        started = self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(started["status"], "provisioning")
        self.call("sandbox.release", project_id=self.project_id, experiment_id=exp_id)
        # Let the gated job unwind; it must honor the cancel, not go running.
        self.backend.gate.set()
        final = self._await_status(exp_id, "terminated")
        self.assertEqual(final["status"], "terminated")

    def test_get_reconciles_orphaned_provisioning(self) -> None:
        # A provisioning row with no in-flight job (daemon restart mid-provision)
        # must reconcile to failed so a polling agent doesn't wait forever.
        exp_id = self._experiment()
        self.app.sandboxes._begin_provisioning_row(
            experiment_id=exp_id,
            project_id=self.project_id,
            req=SandboxRequest(experiment_id=exp_id, project_id=self.project_id, public_key="k"),
        )
        result = self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(result["status"], "failed")

    def test_get_returns_none_when_never_requested(self) -> None:
        exp_id = self._experiment()
        result = self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(result["status"], "none")


if __name__ == "__main__":
    unittest.main()

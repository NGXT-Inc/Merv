"""The control→data task channel, routed in-process (cloud plan Phase 4).

Every byte movement and runtime-teardown signal rides the channel as an
explicit task — initial_push through the new ``awaiting_initial_push`` row
phase, the reaper's final_pull with a deadline, conn_refresh on endpoint
moves, and teardown on terminal rows — while the synchronous in-process
dispatch preserves today's ordering exactly.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.app import ResearchPluginApp
from backend.dataplane.tasks import InProcessTaskChannel
from backend.execution.backends.fake import FakeSandboxBackend
from backend.utils import ValidationError
from tests.fakes import FakeRsyncSyncer


class GatedPushRsyncSyncer(FakeRsyncSyncer):
    """FakeRsyncSyncer whose initial push blocks until the test releases it."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.push_gate = threading.Event()

    def push_initial(self, **kwargs):
        self.push_gate.wait(timeout=10)
        return super().push_initial(**kwargs)


class TaskChannelTestBase(unittest.TestCase):
    rsync_factory = FakeRsyncSyncer

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.backend = FakeSandboxBackend()
        self.rsync = type(self).rsync_factory()
        self.app = ResearchPluginApp(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            execution_backend=self.backend,
            rsync_syncer=self.rsync,
        )
        self.channel: InProcessTaskChannel = self.app.sandboxes.tasks
        self.project_id = self.call("project.create", name="Channel Project")["id"]

    def tearDown(self) -> None:
        self.app.shutdown()
        self.tmp.cleanup()

    def call(self, tool: str, **kwargs):
        return self.app.call_tool(tool, kwargs)

    def _experiment(self) -> str:
        exp_id = self.call(
            "experiment.create", name="exp-1", project_id=self.project_id, intent="x"
        )["id"]
        with self.app.store.transaction() as conn:
            conn.execute(
                "UPDATE experiments SET status = 'ready_to_run' WHERE id = ?", (exp_id,)
            )
        return exp_id

    def _task_types(self) -> list[str]:
        return [task.type for task, _ack in self.channel.history]


class TaskChannelTest(TaskChannelTestBase):
    def test_lifecycle_tasks_dispatch_synchronously_in_order(self) -> None:
        # provision → release drives the full local loop; the channel must
        # observe initial_push, then the release's final_pull, then the
        # terminal teardown — exactly the pre-channel ordering.
        exp_id = self._experiment()
        self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.call("sandbox.release", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(
            self._task_types(), ["initial_push", "final_pull", "teardown"]
        )
        acks = [ack for _task, ack in self.channel.history]
        self.assertTrue(all(ack["ok"] for ack in acks))
        # One ack per task, by id.
        self.assertEqual(
            [ack["task_id"] for _task, ack in self.channel.history],
            [task.id for task, _ack in self.channel.history],
        )
        self.assertEqual(len({task.id for task, _ack in self.channel.history}), 3)

    def test_initial_push_task_carries_the_lease_backed_session(self) -> None:
        exp_id = self._experiment()
        self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        push_task = next(t for t, _a in self.channel.history if t.type == "initial_push")
        session = push_task.payload["session"]
        self.assertEqual(session["experiment_id"], exp_id)
        self.assertEqual(session["remote"]["experiment_dir"], "/workspace/exp-1")
        self.assertEqual(
            session["lease"]["holder_client_id"], self.app.worker.client_id()
        )
        self.assertEqual(
            session["direction_policy"]["artifacts_to_keep"], "remote_append_only"
        )
        # The push itself went through the worker's rsync as before.
        self.assertEqual(self.rsync.push_calls[-1]["remote_sync_dir"], "/workspace/exp-1")

    def test_teardown_task_fires_on_terminal_rows(self) -> None:
        exp_id = self._experiment()
        created = self.call(
            "sandbox.request", project_id=self.project_id, experiment_id=exp_id
        )
        conn_file = self.repo / ".research_plugin" / "sandboxes" / "conn" / exp_id
        self.assertTrue(conn_file.exists())
        stopped: list[str] = []
        original_stop = self.app.worker.stop_dashboards
        self.app.worker.stop_dashboards = lambda *, sandbox_id="": (  # type: ignore[method-assign]
            stopped.append(sandbox_id),
            original_stop(sandbox_id=sandbox_id),
        )
        self.call("sandbox.release", project_id=self.project_id, experiment_id=exp_id)
        teardown = next(t for t, _a in self.channel.history if t.type == "teardown")
        self.assertEqual(teardown.payload["experiment_id"], exp_id)
        self.assertEqual(teardown.payload["sandbox_id"], created["sandbox_id"])
        # The conn file is gone (sbx fails loudly) and the tunnels were stopped.
        self.assertFalse(conn_file.exists())
        self.assertIn(created["sandbox_id"], stopped)

    def test_reaper_final_pull_is_a_task_with_a_deadline(self) -> None:
        exp_id = self._experiment()
        created = self.call(
            "sandbox.request", project_id=self.project_id, experiment_id=exp_id
        )
        state = self.call(
            "experiment.get_state", project_id=self.project_id, experiment_id=exp_id
        )
        self.assertEqual(state["status"], "running")
        with self.app.store.transaction() as conn:
            conn.execute(
                "UPDATE sandboxes SET expires_at=? WHERE experiment_id=?",
                ("2000-01-01T00:00:00Z", exp_id),
            )
        self.assertEqual(self.app.sandboxes.reap_expired(), 1)
        final_pull = next(t for t, _a in self.channel.history if t.type == "final_pull")
        # The deadline is a cloud-minted ISO instant (unenforced in-process —
        # the Phase 5 parachute branch takes over when a daemon misses it).
        self.assertIsNotNone(final_pull.deadline)
        self.assertTrue(str(final_pull.deadline).endswith("Z"))
        self.assertEqual(final_pull.payload["session"]["experiment_id"], exp_id)
        # The pull actually ran before the kill, and the reap still applied
        # the sandbox_expired system transition + termination.
        self.assertTrue(self.rsync.calls)
        self.assertIn(created["sandbox_id"], self.backend.terminated)
        state = self.call(
            "experiment.get_state", project_id=self.project_id, experiment_id=exp_id
        )
        self.assertEqual(state["status"], "ready_to_run")
        got = self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(got["status"], "terminated")

    def test_endpoint_move_emits_a_conn_refresh_task(self) -> None:
        exp_id = self._experiment()
        created = self.call(
            "sandbox.request", project_id=self.project_id, experiment_id=exp_id
        )
        self.backend.move_endpoint(
            sandbox_id=created["sandbox_id"], host="r999.modal.host", port=55555
        )
        self.call("sandbox.get", project_id=self.project_id, experiment_id=exp_id)
        refresh = next(t for t, _a in self.channel.history if t.type == "conn_refresh")
        self.assertEqual(refresh.payload["row"]["ssh_host"], "r999.modal.host")
        # The task re-rendered the conn file the dispatcher sources.
        body = (
            self.repo / ".research_plugin" / "sandboxes" / "conn" / exp_id
        ).read_text()
        self.assertIn("r999.modal.host", body)
        self.assertIn("55555", body)

    def test_worker_refuses_a_session_outside_the_transfer_contract(self) -> None:
        # The direction_policy closes the --delete footgun: a session minted
        # under different per-subtree rules than the rsync flags implement
        # must fail loudly instead of moving bytes wrong.
        exp_id = self._experiment()
        self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        row = self.app.sandboxes.registry.load_row(experiment_id=exp_id)
        session = self.app.sandboxes.sessions.grant_for_row(row=row)
        session["direction_policy"]["artifacts_to_keep"] = "local_authoritative"
        with self.assertRaises(ValidationError):
            self.app.worker.sync_pull(session=session)
        stale_contract = self.app.sandboxes.sessions.grant_for_row(row=row)
        stale_contract["transfer_contract_version"] = 99
        with self.assertRaises(ValidationError):
            self.app.worker.sync_pull(session=stale_contract)

    def test_parachute_restore_unpacks_into_the_experiment_folder(self) -> None:
        # The restore task (plan Phase 5) lands a parachute object at the
        # normal sync target: experiments/<name>/.
        exp_id = self._experiment()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            payload = b'{"accuracy": 0.9}\n'
            info = tarfile.TarInfo(name="./results.json")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        result = self.channel.submit(
            task_type="parachute_restore",
            payload={
                "experiment_id": exp_id,
                "name": "exp-1",
                "data": buffer.getvalue(),
            },
        )
        self.assertEqual(result["restored"], 1)
        self.assertEqual(
            (self.repo / "experiments" / "exp-1" / "results.json").read_bytes(),
            b'{"accuracy": 0.9}\n',
        )
        task, ack = self.channel.history[-1]
        self.assertEqual(task.type, "parachute_restore")
        self.assertTrue(ack["ok"])

    def test_unknown_task_type_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.channel.submit(task_type="reboot_vm", payload={})


class AwaitingInitialPushPhaseTest(TaskChannelTestBase):
    rsync_factory = GatedPushRsyncSyncer

    def _await(self, predicate, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("condition not reached before timeout")

    def test_provision_flows_through_awaiting_initial_push_to_running(self) -> None:
        # Gate the push so the row observably sits in the explicit phase
        # between provider-acquire and running (plan Phase 4).
        self.app.sandboxes.request_wait_seconds = 0.05
        exp_id = self._experiment()
        try:
            result = self.call(
                "sandbox.request", project_id=self.project_id, experiment_id=exp_id
            )
            self.assertEqual(result["status"], "provisioning")
            registry = self.app.sandboxes.registry
            self._await(
                lambda: registry.load_row(experiment_id=exp_id).get("phase")
                == "awaiting_initial_push"
            )
            row = registry.load_row(experiment_id=exp_id)
            # Status stays `provisioning`, so cancellation and the reconcile/
            # orphan-cleanup paths cover the new phase like any other.
            self.assertEqual(row["status"], "provisioning")
            # The phase is agent-visible the way other phases already are.
            polled = self.call(
                "sandbox.get", project_id=self.project_id, experiment_id=exp_id
            )
            self.assertEqual(polled["status"], "provisioning")
            self.assertEqual(polled["phase"], "awaiting_initial_push")
        finally:
            self.rsync.push_gate.set()
        self._await(
            lambda: registry.load_row(experiment_id=exp_id).get("status") == "running"
        )
        row = registry.load_row(experiment_id=exp_id)
        self.assertEqual(row["phase"], "")
        self.assertEqual(self._task_types(), ["initial_push"])

    def test_release_during_awaiting_initial_push_cancels_cleanly(self) -> None:
        # A release that lands mid-push must terminate the VM, never mark the
        # row running — the cancellation path covers the new phase.
        self.app.sandboxes.request_wait_seconds = 0.05
        exp_id = self._experiment()
        registry = self.app.sandboxes.registry
        try:
            self.call(
                "sandbox.request", project_id=self.project_id, experiment_id=exp_id
            )
            self._await(
                lambda: registry.load_row(experiment_id=exp_id).get("phase")
                == "awaiting_initial_push"
            )
            self.call(
                "sandbox.release", project_id=self.project_id, experiment_id=exp_id
            )
        finally:
            self.rsync.push_gate.set()
        self._await(
            lambda: registry.load_row(experiment_id=exp_id).get("status")
            == "terminated"
        )
        self.assertTrue(self.backend.terminated)


if __name__ == "__main__":
    unittest.main()

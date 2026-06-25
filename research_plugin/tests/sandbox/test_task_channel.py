"""The control→data task channel, routed in-process (cloud plan Phase 4).

Every byte movement and runtime-teardown signal rides the channel as an
explicit task — sync_pull, the reaper's final_pull with a deadline,
conn_refresh on endpoint moves, and teardown on terminal rows — while the
synchronous in-process dispatch preserves today's ordering exactly.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from backend.app import ResearchPluginApp
from backend.dataplane.tasks import InProcessTaskChannel
from backend.execution.backends.fake import FakeSandboxBackend
from backend.utils import ValidationError
from tests.fakes import FakeRsyncSyncer


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
        # observe the release's final_pull, then the terminal teardown.
        exp_id = self._experiment()
        self.call("sandbox.request", project_id=self.project_id, experiment_id=exp_id)
        self.call("sandbox.release", project_id=self.project_id, experiment_id=exp_id)
        self.assertEqual(self._task_types(), ["final_pull", "teardown"])
        acks = [ack for _task, ack in self.channel.history]
        self.assertTrue(all(ack["ok"] for ack in acks))
        # One ack per task, by id.
        self.assertEqual(
            [ack["task_id"] for _task, ack in self.channel.history],
            [task.id for task, _ack in self.channel.history],
        )
        self.assertEqual(len({task.id for task, _ack in self.channel.history}), 2)

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

    def test_parachute_restore_can_download_from_url(self) -> None:
        exp_id = self._experiment()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            payload = b'{"loss": 0.1}\n'
            info = tarfile.TarInfo(name="./metrics.json")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        archive = self.repo / "parachute.tgz"
        archive.write_bytes(buffer.getvalue())

        result = self.channel.submit(
            task_type="parachute_restore",
            payload={
                "experiment_id": exp_id,
                "name": "exp-1",
                "get_url": archive.resolve().as_uri(),
            },
        )

        self.assertEqual(result["restored"], 1)
        self.assertEqual(
            (self.repo / "experiments" / "exp-1" / "metrics.json").read_bytes(),
            b'{"loss": 0.1}\n',
        )

    def test_unknown_task_type_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.channel.submit(task_type="reboot_vm", payload={})


if __name__ == "__main__":
    unittest.main()

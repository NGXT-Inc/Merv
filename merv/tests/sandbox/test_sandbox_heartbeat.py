from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support.brain import TestBrain
from merv.brain.sandbox.execution.backends.fake import FakeSandboxBackend
from merv.brain.sandbox.sandbox_daemons import SandboxDaemons
from merv.brain.sandbox.sandbox_backend import BackendCapabilities
from merv.brain.sandbox.sandbox_heartbeat import SandboxActivityPolicy, SandboxIdlePolicy
from merv.brain.kernel.utils import format_iso


def _sample(
    *,
    cpu: float = 0.0,
    gpu: int = 0,
    mem: int = 1_000_000,
    net: int = 1_000,
    ssh: int = 0,
) -> dict:
    return {
        "cpu": {"used_cores": cpu, "limit_cores": 2.0},
        "memory": {"used_bytes": mem, "limit_bytes": None},
        "network": {"bytes_total": net, "ssh_established": ssh},
        "gpus": [{"index": 0, "util_pct": gpu}],
    }


class SandboxIdlePolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = SandboxIdlePolicy()
        self.previous = _sample()

    def test_all_quiet_is_idle(self) -> None:
        self.assertTrue(
            self.policy.is_idle(
                current=_sample(),
                previous=self.previous,
                elapsed_seconds=30,
            )
        )

    def test_work_in_flight_outranks_every_quiet_gauge(self) -> None:
        self.assertFalse(
            self.policy.is_idle(
                current=_sample(),
                previous=self.previous,
                elapsed_seconds=30,
                work_running=True,
            )
        )

    def test_unmeasurable_ssh_does_not_block_idle(self) -> None:
        # ss/proc absent (e.g. Modal has no sshd) → ssh_established is None;
        # that must not make an otherwise-quiet box un-reapable.
        self.assertTrue(
            self.policy.is_idle(
                current=_sample(ssh=None),
                previous=self.previous,
                elapsed_seconds=30,
            )
        )

    def test_any_activity_signal_is_not_idle(self) -> None:
        cases = {
            "network": _sample(net=100_000),
            "cpu": _sample(cpu=0.25),
            "gpu": _sample(gpu=20),
            "ram": _sample(mem=100_000_000),
            "ssh": _sample(ssh=1),
        }
        for name, current in cases.items():
            with self.subTest(signal=name):
                self.assertFalse(
                    self.policy.is_idle(
                        current=current,
                        previous=self.previous,
                        elapsed_seconds=30,
                    )
                )

    def test_idle_window_accumulates_to_reap_threshold(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        started = self.policy.next_idle_since(idle_since=None, now=now, is_idle=True)
        self.assertEqual(started, now)
        self.assertFalse(
            self.policy.should_reap(
                idle_since=now - timedelta(seconds=3599),
                now=now,
                threshold_seconds=3600,
            )
        )
        self.assertTrue(
            self.policy.should_reap(
                idle_since=now - timedelta(seconds=3600),
                now=now,
                threshold_seconds=3600,
            )
        )

    def test_activity_resets_idle_window(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        self.assertIsNone(
            self.policy.next_idle_since(
                idle_since=now - timedelta(hours=2),
                now=now,
                is_idle=False,
            )
        )


class SandboxActivityPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = SandboxActivityPolicy()
        self.previous = _sample()

    def test_running_command_counts_as_activity(self) -> None:
        self.assertTrue(
            self.policy.is_active(
                current=_sample(),
                previous=self.previous,
                elapsed_seconds=30,
                command={"status": "running"},
            )
        )

    def test_old_low_threshold_examples_do_not_extend(self) -> None:
        cases = {
            "cpu": _sample(cpu=0.10),
            "gpu": _sample(gpu=5),
            "network": _sample(net=200_000),
            "memory": _sample(mem=200_000_000),
            "ssh": _sample(ssh=1),
        }
        for name, current in cases.items():
            with self.subTest(signal=name):
                self.assertFalse(
                    self.policy.is_active(
                        current=current,
                        previous=self.previous,
                        elapsed_seconds=30,
                    )
                )

    def test_higher_activity_thresholds_extend(self) -> None:
        cases = {
            "cpu": _sample(cpu=0.30),
            "gpu": _sample(gpu=25),
            "network": _sample(net=4_000_000),
            "memory": _sample(mem=900_000_000),
        }
        for name, current in cases.items():
            with self.subTest(signal=name):
                self.assertTrue(
                    self.policy.is_active(
                        current=current,
                        previous=self.previous,
                        elapsed_seconds=30,
                    )
                )

    def test_stored_snapshot_uses_previous_sample_for_deltas(self) -> None:
        self.assertTrue(
            self.policy.is_active_snapshot(
                snapshot={
                    "sampled_at": "2026-06-09T12:00:30Z",
                    "metrics": _sample(net=4_000_000),
                    "previous_sampled_at": "2026-06-09T12:00:00Z",
                    "previous_metrics": self.previous,
                }
            )
        )


class SandboxHeartbeatMonitorTest(unittest.TestCase):
    _ENV = {"RESEARCH_PLUGIN_SANDBOX_REAPER_INTERVAL": "3600"}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self._saved = {key: os.environ.get(key) for key in self._ENV}
        os.environ.update(self._ENV)
        self.backend = FakeSandboxBackend()
        self.app = TestBrain(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            execution_backend=self.backend,
        )
        self.project_id = self.app.call_tool(
            "project", {"action": "create", "name": "Heartbeat Project"}
        )["id"]

    def tearDown(self) -> None:
        self.app.shutdown()
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _experiment(self, name: str) -> str:
        exp_id = self.app.call_tool(
            "experiment.create",
            {"project_id": self.project_id, "name": name, "intent": "x"},
        )["id"]
        with self.app.store.transaction() as conn:
            conn.execute(
                "UPDATE experiments SET status = 'ready_to_run' WHERE id = ?", (exp_id,)
            )
        return exp_id

    def _request(self, exp_id: str) -> dict:
        return self.app.call_tool(
            "sandbox.request",
            {"project_id": self.project_id, "experiment_id": exp_id},
        )

    def _seed_heartbeat(
        self,
        *,
        exp_id: str,
        sandbox_uid: str,
        sampled_at: datetime,
        idle_since: datetime,
        metrics: dict,
    ) -> None:
        self.app.sandboxes.repository.record_heartbeat(
            experiment_id=exp_id,
            sandbox_uid=sandbox_uid,
            expected_project_id=self.project_id,
            idle_since=format_iso(idle_since),
            snapshot={"sampled_at": format_iso(sampled_at), "metrics": metrics},
        )

    def test_idle_sandbox_is_reaped_while_busy_sandbox_is_spared(self) -> None:
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        previous_at = now - timedelta(seconds=30)
        idle_since = now - timedelta(hours=1)
        idle_exp = self._experiment("idle")
        busy_exp = self._experiment("busy")
        idle = self._request(idle_exp)
        busy = self._request(busy_exp)
        for exp_id, sandbox_uid in (
            (idle_exp, idle["sandbox_uid"]),
            (busy_exp, busy["sandbox_uid"]),
        ):
            self._seed_heartbeat(
                exp_id=exp_id,
                sandbox_uid=str(sandbox_uid),
                sampled_at=previous_at,
                idle_since=idle_since,
                metrics=_sample(),
            )
        self.backend.metrics[idle["sandbox_id"]] = _sample()
        self.backend.metrics[busy["sandbox_id"]] = _sample(cpu=0.5)

        reaped = self.app.sandboxes.reap_idle(now=now, threshold_seconds=3600)

        self.assertEqual(reaped, 1)
        self.assertIn(idle["sandbox_id"], self.backend.terminated)
        self.assertNotIn(busy["sandbox_id"], self.backend.terminated)
        self.assertEqual(
            self.app.sandboxes.get(
                project_id=self.project_id,
                sandbox_uid=str(idle["sandbox_uid"]),
            )["status"],
            "terminated",
        )
        self.assertEqual(
            self.app.sandboxes.get(project_id=self.project_id, experiment_id=busy_exp)[
                "status"
            ],
            "running",
        )
        events = self.app.store.recent_events(project_id=self.project_id)["events"]
        idle_events = [
            event
            for event in events
            if event["type"] == "sandbox.idle_reaped" and event["target_id"] == idle_exp
        ]
        self.assertEqual(len(idle_events), 1)
        self.assertEqual(idle_events[0]["payload"]["idle_seconds"], 3600)

    def _idle_candidate(self, name: str, *, now: datetime) -> dict:
        """A running sandbox that every sampled gauge calls idle."""
        exp_id = self._experiment(name)
        created = self._request(exp_id)
        self._seed_heartbeat(
            exp_id=exp_id,
            sandbox_uid=str(created["sandbox_uid"]),
            sampled_at=now - timedelta(seconds=30),
            idle_since=now - timedelta(hours=1),
            metrics=_sample(),
        )
        self.backend.metrics[created["sandbox_id"]] = _sample()
        return {**created, "experiment_id": exp_id}

    def _status(self, sandbox_uid: str) -> str:
        return str(
            self.app.sandboxes.repository.get_by_uid(sandbox_uid=sandbox_uid)["status"]
        )

    def test_a_running_merv_run_receipt_vetoes_the_idle_reap(self) -> None:
        # The gauges say idle, but a detached run never reported finishing:
        # a blocked download or low-CPU orchestration step looks exactly like
        # this, and reaping it destroys the work (audit SAN-07).
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        idle = self._idle_candidate("receipt-veto", now=now)
        with self.app.store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sandbox_runs (
                  sandbox_uid, label, command, exit_code, started_at,
                  first_seen_at, updated_at
                ) VALUES (?, 'train', 'python train.py', NULL, ?, ?, ?)
                """,
                (
                    str(idle["sandbox_uid"]),
                    format_iso(now - timedelta(minutes=30)),
                    format_iso(now - timedelta(minutes=30)),
                    format_iso(now - timedelta(seconds=30)),
                ),
            )

        self.assertEqual(
            self.app.sandboxes.reap_idle(now=now, threshold_seconds=3600), 0
        )
        self.assertNotIn(idle["sandbox_id"], self.backend.terminated)
        self.assertEqual(self._status(str(idle["sandbox_uid"])), "running")
        # Work in flight also resets the idle clock rather than banking it.
        self.assertIsNone(
            self.app.sandboxes.repository.get_by_uid(
                sandbox_uid=str(idle["sandbox_uid"])
            )["idle_since"]
        )

    def test_a_finished_receipt_does_not_veto_the_idle_reap(self) -> None:
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        idle = self._idle_candidate("receipt-finished", now=now)
        with self.app.store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sandbox_runs (
                  sandbox_uid, label, command, exit_code, started_at,
                  first_seen_at, updated_at
                ) VALUES (?, 'train', 'python train.py', 0, ?, ?, ?)
                """,
                (
                    str(idle["sandbox_uid"]),
                    format_iso(now - timedelta(hours=3)),
                    format_iso(now - timedelta(hours=3)),
                    format_iso(now - timedelta(seconds=30)),
                ),
            )

        self.assertEqual(
            self.app.sandboxes.reap_idle(now=now, threshold_seconds=3600), 1
        )
        self.assertIn(idle["sandbox_id"], self.backend.terminated)

    def test_a_stale_receipt_no_longer_vetoes(self) -> None:
        # The ledger has not re-confirmed this run in longer than the whole
        # idle window: the run directory is gone, so it is not evidence of work.
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        idle = self._idle_candidate("receipt-stale", now=now)
        with self.app.store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sandbox_runs (
                  sandbox_uid, label, command, exit_code, started_at,
                  first_seen_at, updated_at
                ) VALUES (?, 'train', 'python train.py', NULL, ?, ?, ?)
                """,
                (
                    str(idle["sandbox_uid"]),
                    format_iso(now - timedelta(days=2)),
                    format_iso(now - timedelta(days=2)),
                    format_iso(now - timedelta(days=2)),
                ),
            )

        self.assertEqual(
            self.app.sandboxes.reap_idle(now=now, threshold_seconds=3600), 1
        )

    def test_a_receipt_that_exists_only_on_the_box_vetoes_the_idle_reap(self) -> None:
        # The blocker: a quiet merv_run launched right after the last mirror
        # sweep exists ONLY on the sandbox. Deciding against the mirror alone
        # reaps a machine that is working — so the candidate's receipts are
        # read from the box before the decision, not a tick later.
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        idle = self._idle_candidate("on-box-only", now=now)
        # Raw listing exactly as the on-box command emits it: a run with no
        # ===EXIT sentinel is still in flight. Nothing is in sandbox_runs yet.
        self.backend.run_listings[idle["sandbox_id"]] = (
            "===MERV_RUN dHJhaW4=\n"
            "===META eyJsYWJlbCI6InRyYWluIiwiY29tbWFuZCI6InB5dGhvbiB0cmFpbi5weSJ9\n"
        )
        with self.app.store.transaction() as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sandbox_runs WHERE sandbox_uid = ?",
                    (str(idle["sandbox_uid"]),),
                ).fetchone()
            )

        self.assertEqual(
            self.app.sandboxes.reap_idle(now=now, threshold_seconds=3600), 0
        )
        self.assertNotIn(idle["sandbox_id"], self.backend.terminated)
        self.assertEqual(self._status(str(idle["sandbox_uid"])), "running")

    def test_an_unreadable_receipt_source_vetoes_the_idle_reap(self) -> None:
        # A known running receipt whose ledger has not been refreshable for
        # longer than the whole idle window: its updated_at ages out of the
        # freshness query, so the mirror alone now says "nothing running".
        # That silence is ignorance, not proof — read_runs returning None must
        # veto rather than license the reap.
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        idle = self._idle_candidate("unreadable", now=now)
        with self.app.store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sandbox_runs (
                  sandbox_uid, label, command, exit_code, started_at,
                  first_seen_at, updated_at
                ) VALUES (?, 'train', 'python train.py', NULL, ?, ?, ?)
                """,
                (
                    str(idle["sandbox_uid"]),
                    format_iso(now - timedelta(days=2)),
                    format_iso(now - timedelta(days=2)),
                    format_iso(now - timedelta(days=2)),
                ),
            )

        def unreadable(*, sandbox_id, workdir="", ssh_host="", ssh_port=0,
                       ssh_user="", key_path=""):
            return None  # management channel down: "no news", not "no runs"

        self.backend.read_runs = unreadable  # type: ignore[assignment]

        self.assertEqual(
            self.app.sandboxes.reap_idle(now=now, threshold_seconds=3600), 0
        )
        self.assertNotIn(idle["sandbox_id"], self.backend.terminated)
        self.assertEqual(self._status(str(idle["sandbox_uid"])), "running")

    def test_a_failed_receipt_mirror_write_does_not_license_a_reap(self) -> None:
        # The box answered, but the mirror write blew up. A receipt we saw and
        # could not record is still work in flight.
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        idle = self._idle_candidate("unmirrored", now=now)
        self.backend.run_listings[idle["sandbox_id"]] = (
            "===MERV_RUN dHJhaW4=\n"
            "===META eyJsYWJlbCI6InRyYWluIiwiY29tbWFuZCI6InB5dGhvbiB0cmFpbi5weSJ9\n"
        )
        ledger = self.app.sandboxes.runs_ledger

        def exploding_record(*, row, listing):
            raise RuntimeError("state store unreachable")

        with patch.object(ledger, "_record", side_effect=exploding_record):
            self.assertEqual(
                self.app.sandboxes.reap_idle(now=now, threshold_seconds=3600), 0
            )
        self.assertNotIn(idle["sandbox_id"], self.backend.terminated)

    def test_a_running_command_vetoes_the_idle_reap(self) -> None:
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        idle = self._idle_candidate("command-veto", now=now)
        self.app.sandboxes.repository.record_command_snapshot(
            sandbox_uid=str(idle["sandbox_uid"]),
            snapshot={"command_id": "cmd_1", "command": "bash setup.sh", "status": "running"},
            expected_project_id=self.project_id,
        )

        self.assertEqual(
            self.app.sandboxes.reap_idle(now=now, threshold_seconds=3600), 0
        )
        self.assertNotIn(idle["sandbox_id"], self.backend.terminated)

    def test_an_unconfirmed_deletion_parks_the_reap_and_reports_no_reap(self) -> None:
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        idle = self._idle_candidate("unconfirmed", now=now)
        self.backend.terminate = lambda *, sandbox_id: False  # type: ignore[assignment]
        self.backend.liveness_unavailable = True

        self.assertEqual(
            self.app.sandboxes.reap_idle(now=now, threshold_seconds=3600), 0
        )
        self.assertEqual(
            self._status(str(idle["sandbox_uid"])), "cleanup_pending"
        )

    def test_a_row_that_left_running_mid_sweep_is_not_reaped(self) -> None:
        # The re-read guard: the snapshot ages while earlier rows make provider
        # calls, and the row may already be gone by the time this one's turn
        # comes up.
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        idle = self._idle_candidate("raced", now=now)
        repository = self.app.sandboxes.repository
        original_get = repository.get_by_uid

        def get_by_uid(*, sandbox_uid):
            row = original_get(sandbox_uid=sandbox_uid)
            return {**row, "status": "terminated"}

        with patch.object(repository, "get_by_uid", side_effect=get_by_uid):
            self.assertEqual(
                self.app.sandboxes.reap_idle(now=now, threshold_seconds=3600), 0
            )
        self.assertNotIn(idle["sandbox_id"], self.backend.terminated)

    def test_zero_threshold_disables_idle_reaping(self) -> None:
        exp_id = self._experiment("disabled")
        created = self._request(exp_id)
        now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
        self._seed_heartbeat(
            exp_id=exp_id,
            sandbox_uid=str(created["sandbox_uid"]),
            sampled_at=now - timedelta(seconds=30),
            idle_since=now - timedelta(hours=2),
            metrics=_sample(),
        )
        self.backend.metrics[created["sandbox_id"]] = _sample()

        self.assertEqual(
            self.app.sandboxes.reap_idle(now=now, threshold_seconds=0),
            0,
        )
        self.assertNotIn(created["sandbox_id"], self.backend.terminated)


class SandboxSweepOrderTest(unittest.TestCase):
    """The reaper's tick order is load-bearing, not incidental."""

    def test_receipts_are_reconciled_before_the_idle_decision(self) -> None:
        trace: list[str] = []
        lifecycle = SimpleNamespace(
            reap_row=lambda **_kwargs: True,
            reap_expired=lambda **_kwargs: trace.append("expiry"),
            retry_cleanup_pending=lambda **_kwargs: trace.append("cleanup_retry"),
        )
        daemons = SandboxDaemons(
            repository=object(),  # type: ignore[arg-type]
            # MinimalBackend-like: enforce_expiry defaults on, so the expiry
            # and stale-provision sweeps actually run in this tick.
            backend=SimpleNamespace(
                capabilities=BackendCapabilities(name="stub"),
            ),  # type: ignore[arg-type]
            provisioner=SimpleNamespace(
                reap_stale_provisions=lambda **_kwargs: trace.append("stale")
            ),  # type: ignore[arg-type]
            lifecycle=lifecycle,  # type: ignore[arg-type]
            sample_metrics=lambda **_kwargs: {},
            reconcile_runs=lambda: trace.append("receipts"),
        )
        daemons.reap_idle = lambda **_kwargs: trace.append("idle")  # type: ignore[assignment]

        with patch.dict(
            os.environ,
            {"RESEARCH_PLUGIN_SANDBOX_REAPER": "1"},
            clear=False,
        ):
            daemons.sweep_once(stale_deadline_seconds=900.0)

        self.assertEqual(
            trace, ["expiry", "receipts", "idle", "stale", "cleanup_retry"]
        )
        self.assertLess(trace.index("receipts"), trace.index("idle"))


class SandboxHeartbeatEnvTest(unittest.TestCase):
    def _daemons(self) -> SandboxDaemons:
        return SandboxDaemons(
            repository=object(),  # type: ignore[arg-type]
            backend=FakeSandboxBackend(),
            provisioner=object(),  # type: ignore[arg-type]
            lifecycle=SimpleNamespace(reap_row=lambda **_kwargs: True),  # type: ignore[arg-type]
            sample_metrics=lambda **_kwargs: {},
        )

    def test_idle_threshold_zero_or_empty_disables_idle_reaping(self) -> None:
        daemons = self._daemons()
        for value in ("0", ""):
            with self.subTest(value=value):
                with patch.dict(
                    os.environ,
                    {"RESEARCH_PLUGIN_SANDBOX_IDLE_SECONDS": value},
                    clear=False,
                ):
                    self.assertEqual(daemons._idle_reap_threshold(), 0)


if __name__ == "__main__":
    unittest.main()

"""Cloud cleanup sweeps (cloud plan Phase 9), driven by injected clocks.

The idempotent sweeps grouped behind CleanupService.run_all — orphan-VM,
blob TTL GC, storage TTL GC, and stale-provision reap — each take a
``now`` so the test owns the clock. The service is mode-blind (the in-process
app exercises the exact code the control plane schedules), so these run without
docker or a real control plane.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.support.brain import DEFAULT_PUBLIC_KEY, TestBrain
from merv.brain.kernel.utils import parse_iso
from merv.brain.sandbox.execution.backends.fake import FakeSandboxBackend
from merv.brain.sandbox.sandbox_backend import BackendCapabilities
from merv.brain.application.maintenance import CleanupService


class CleanupSweepTest(unittest.TestCase):
    # Park the background reaper so the test, not a timer, drives every sweep.
    _ENV = {
        "RESEARCH_PLUGIN_SANDBOX_REAPER_INTERVAL": "3600",
        "RESEARCH_PLUGIN_SANDBOX_REAPER": "0",
    }

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self._saved = {k: os.environ.get(k) for k in self._ENV}
        os.environ.update(self._ENV)
        self.backend = FakeSandboxBackend()
        # enforce_expiry off keeps the reaper inert; the sweeps drive themselves.
        self.backend.capabilities = BackendCapabilities(name="fake")
        self.app = TestBrain(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            execution_backend=self.backend,
        )
        self.store = self.app.store
        self.cleanup = CleanupService(
            sandboxes=self.app.sandboxes, blobs=self.app.blobs
        )
        self.project_id = self.app.call_tool("project", {"action": "create", "name": "Proj C"})["id"]

    def tearDown(self) -> None:
        self.app.shutdown()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _experiment(self) -> str:
        return self.app.call_tool(
            "experiment.create",
            {"project_id": self.project_id, "name": "exp", "intent": "x"},
        )["id"]

    # ---- orphan-VM sweep ----

    def test_orphan_vm_sweep_reaps_a_running_row_whose_vm_is_gone(self) -> None:
        exp_id = self._experiment()
        sandbox_uid = "uid_gone"
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid=sandbox_uid,
            project_id=self.project_id,
            sandbox_id="sb-gone",
            status="running",
            ssh_host="h",
            ssh_port=22,
            ssh_user="root",
            expires_at="2999-01-01T00:00:00Z",
        )
        # The provider says the VM is gone (never marked alive in the fake).
        self.assertFalse(self.backend.is_alive(sandbox_id="sb-gone"))
        reaped = self.cleanup.sweep_orphan_vms(now=datetime.now(tz=UTC))
        self.assertEqual(reaped, 1)
        row = self.app.sandboxes.repository.get_by_uid(sandbox_uid=sandbox_uid)
        self.assertEqual(row["status"], "terminated")

    def test_orphan_vm_sweep_leaves_a_live_row_running(self) -> None:
        exp_id = self._experiment()
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid="uid_live",
            project_id=self.project_id,
            sandbox_id="sb-live",
            status="running",
            ssh_host="h",
            ssh_port=22,
            ssh_user="root",
            expires_at="2999-01-01T00:00:00Z",
        )
        self.backend.alive["sb-live"] = True
        reaped = self.cleanup.sweep_orphan_vms(now=datetime.now(tz=UTC))
        self.assertEqual(reaped, 0)
        row = self.app.sandboxes.repository.load_row(experiment_id=exp_id)
        self.assertEqual(row["status"], "running")

    # ---- blob TTL GC ----

    def test_blob_ttl_gc_deletes_expired_blobs(self) -> None:
        ns = self.project_id
        live = self.app.blobs.put(
            namespace=ns, data=b"keep", expires_at="2999-01-01T00:00:00Z"
        )
        dead = self.app.blobs.put(
            namespace=ns, data=b"drop", expires_at="2000-01-01T00:00:00Z"
        )
        swept = self.cleanup.sweep_expired_blobs(now=datetime.now(tz=UTC))
        self.assertEqual(swept, {"deleted": 1, "ok": True})
        self.assertIsNotNone(self.app.blobs.stat(namespace=ns, sha256=live))
        self.assertIsNone(self.app.blobs.stat(namespace=ns, sha256=dead))

    # ---- stale provisioning reap ----

    def test_stale_provision_reaped_past_deadline(self) -> None:
        exp_id = self._experiment()
        sandbox_uid = "uid_wedged"
        started = "2026-01-01T00:00:00Z"
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid=sandbox_uid,
            project_id=self.project_id,
            sandbox_id="sb-wedged",
            status="provisioning",
            phase="connecting",
            provision_started_at=started,
        )
        self.backend.alive["sb-wedged"] = True
        self.backend.by_experiment[exp_id] = "sb-wedged"
        # 20 minutes later, well past the stale-provision deadline.
        now = datetime(2026, 1, 1, 0, 20, 0, tzinfo=UTC)
        reaped = self.cleanup.sweep_stale_provisions(now=now)
        self.assertEqual(reaped, 1)
        row = self.app.sandboxes.repository.get_by_uid(sandbox_uid=sandbox_uid)
        self.assertEqual(row["status"], "failed")
        # The billing VM was terminated by cleanup_orphan.
        self.assertIn("sb-wedged", self.backend.terminated)

    def test_stale_provision_reaped_in_earlier_phase(self) -> None:
        # A daemon crash during `connecting` (Lambda waiting for boot + SSH)
        # leaves a billing VM in a provisioning phase. The sweep must still
        # reap it — the VM exists from `creating` onward.
        exp_id = self._experiment()
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid="uid_connecting",
            project_id=self.project_id,
            sandbox_id="sb-connecting",
            status="provisioning",
            phase="connecting",
            provision_started_at="2026-01-01T00:00:00Z",
        )
        self.backend.alive["sb-connecting"] = True
        self.backend.by_experiment[exp_id] = "sb-connecting"
        now = datetime(2026, 1, 1, 0, 20, 0, tzinfo=UTC)
        reaped = self.cleanup.sweep_stale_provisions(now=now)
        self.assertEqual(reaped, 1)
        row = self.app.sandboxes.repository.get_by_uid(sandbox_uid="uid_connecting")
        self.assertEqual(row["status"], "failed")
        self.assertIn("sb-connecting", self.backend.terminated)

    def test_stale_provision_reaped_before_id_recorded(self) -> None:
        # Crash in the narrow window after the provider created the VM but
        # before on_created persisted its id: the row has an empty sandbox_id,
        # so the reap can only find the VM by its deterministic name
        # (cleanup_orphan -> backend.find_sandbox_id). It must still be killed.
        exp_id = self._experiment()
        sandbox_uid = "uid_unrecorded"
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid=sandbox_uid,
            project_id=self.project_id,
            sandbox_id="",
            status="provisioning",
            phase="creating",
            provision_started_at="2026-01-01T00:00:00Z",
        )
        # Only the deterministic-name lookup knows about this VM.
        self.backend.alive["sb-unrecorded"] = True
        self.backend.by_experiment[exp_id] = "sb-unrecorded"
        now = datetime(2026, 1, 1, 0, 20, 0, tzinfo=UTC)
        reaped = self.cleanup.sweep_stale_provisions(now=now)
        self.assertEqual(reaped, 1)
        row = self.app.sandboxes.repository.get_by_uid(sandbox_uid=sandbox_uid)
        self.assertEqual(row["status"], "failed")
        self.assertIn("sb-unrecorded", self.backend.terminated)

    def test_stale_provision_left_alone_within_deadline(self) -> None:
        exp_id = self._experiment()
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid="uid_fresh",
            project_id=self.project_id,
            sandbox_id="sb-fresh",
            status="provisioning",
            phase="connecting",
            provision_started_at="2026-01-01T00:00:00Z",
        )
        # Only 2 minutes in — under the deadline, so it keeps provisioning.
        now = datetime(2026, 1, 1, 0, 2, 0, tzinfo=UTC)
        reaped = self.cleanup.sweep_stale_provisions(now=now)
        self.assertEqual(reaped, 0)
        row = self.app.sandboxes.repository.load_row(experiment_id=exp_id)
        self.assertEqual(row["status"], "provisioning")

    # ---- run_all ----

    def test_run_all_returns_per_sweep_counts_and_is_idempotent(self) -> None:
        # One expired blob + one dead-VM row.
        self.app.blobs.put(
            namespace=self.project_id, data=b"x", expires_at="2000-01-01T00:00:00Z"
        )
        exp_id = self._experiment()
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid="uid_dead_run_all",
            project_id=self.project_id,
            sandbox_id="sb-dead",
            status="running",
            expires_at="2999-01-01T00:00:00Z",
        )
        future = datetime.now(tz=UTC) + timedelta(hours=1)
        report = self.cleanup.run_all(now=future)
        self.assertEqual(report.orphan_vms_reaped, 1)
        self.assertEqual(report.blobs_swept, {"deleted": 1, "ok": True})
        # A second pass over the cleaned state changes nothing.
        report2 = self.cleanup.run_all(now=future)
        skipped = {"deleted": 0, "ok": True, "skipped": True}
        self.assertEqual(report2.as_dict(), {
            "ok": True,
            "orphan_vms_reaped": 0,
            # Nothing parked, so the money-safety sweep reports a clean pass.
            "cleanup_pending": {
                "ok": True, "pending": 0, "confirmed": 0, "retried": 0
            },
            "blobs_swept": {"deleted": 0, "ok": True},
            # No storage, ledger, or OAuth store wired into this CleanupService,
            # and the report says skipped rather than reporting a sweep that
            # never ran as a clean zero.
            "storage_objects_swept": skipped,
            "stale_provisions_reaped": 0,
            "tool_calls_pruned": skipped,
            "oauth_clients_pruned": skipped,
        })

    # ---- tool-call ledger retention ----

    def test_prune_deletes_expired_ledger_rows_through_the_pass(self) -> None:
        cleanup = CleanupService(
            sandboxes=self.app.sandboxes,
            blobs=self.app.blobs,
            tool_call_ledger=self.app.tool_ledger,
        )
        self.app.call_tool("claim.list", {"project_id": self.project_id})
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO tool_calls (ts, tool, source, status) VALUES (?, ?, ?, ?)",
                ("2020-01-01T00:00:00Z", "ancient", "mcp", "ok"),
            )
        outcome = cleanup.run_all(now=datetime.now(tz=UTC)).as_dict()["tool_calls_pruned"]
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["deleted"], 1)
        with self.store.transaction() as conn:
            remaining = [
                str(row["tool"])
                for row in conn.execute("SELECT tool FROM tool_calls").fetchall()
            ]
        self.assertNotIn("ancient", remaining)

    def test_a_failing_blob_sweep_is_reported_as_not_ok_not_as_zero(self) -> None:
        class ExplodingBlobs:
            def sweep_expired(self, *, now):
                raise RuntimeError("blob store unreachable")

        class ExplodingStorage:
            def sweep_expired(self, *, now):
                raise RuntimeError("bucket unreachable")

        cleanup = CleanupService(
            sandboxes=self.app.sandboxes,
            blobs=ExplodingBlobs(),
            storage=ExplodingStorage(),
        )
        report = cleanup.run_all(now=datetime.now(tz=UTC))
        self.assertEqual(
            report.blobs_swept,
            {"deleted": 0, "ok": False, "error": "blob store unreachable"},
        )
        self.assertEqual(
            report.storage_objects_swept,
            {"deleted": 0, "ok": False, "error": "bucket unreachable"},
        )
        # The pass still completes, and the response says it did not go clean.
        self.assertFalse(report.ok)
        self.assertIs(report.as_dict()["ok"], False)
        self.assertEqual(report.orphan_vms_reaped, 0)

    # ---- unconfirmed deletions stay visible (audit SAN-05/SAN-06) ----

    def _running_row(self, *, sandbox_uid: str, sandbox_id: str) -> str:
        exp_id = self._experiment()
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid=sandbox_uid,
            project_id=self.project_id,
            sandbox_id=sandbox_id,
            status="running",
            expires_at="2000-01-01T00:00:00Z",
        )
        self.backend.alive[sandbox_id] = True
        return exp_id

    def _row(self, sandbox_uid: str) -> dict:
        return self.app.sandboxes.repository.get_by_uid(sandbox_uid=sandbox_uid)

    def _sandbox_events(self, event_type: str) -> list[dict]:
        return [
            event
            for event in self.store.recent_events(project_id=self.project_id)["events"]
            if event["type"] == event_type
        ]

    def test_a_delete_that_raises_parks_the_row_instead_of_terminating_it(self) -> None:
        uid = "uid_delete_raises"
        self._running_row(sandbox_uid=uid, sandbox_id="sb-delete-raises")

        def exploding_terminate(*, sandbox_id):
            raise RuntimeError("provider API 503")

        self.backend.terminate = exploding_terminate  # type: ignore[assignment]
        self.backend.liveness_unavailable = True
        self.assertEqual(
            self.app.sandboxes.reap_expired(now=datetime(2999, 1, 1, tzinfo=UTC)), 0
        )

        row = self._row(uid)
        self.assertEqual(row["status"], "cleanup_pending")
        self.assertEqual(row["phase"], "cleanup_attempt_1")
        self.assertIn("may still exist and bill", row["detail"])
        # Durable ledger entry, and visible in the project's sandbox list.
        events = self._sandbox_events("sandbox.cleanup_pending")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["trigger"], "expired")
        listed = self.app.sandboxes.list_sandboxes(project_id=self.project_id)
        self.assertIn(
            "cleanup_pending",
            [entry["status"] for entry in listed["sandboxes"]],
        )

    def test_the_retry_terminalizes_once_the_provider_confirms_gone(self) -> None:
        uid = "uid_retry_confirms"
        self._running_row(sandbox_uid=uid, sandbox_id="sb-retry-confirms")
        original_terminate = self.backend.terminate
        self.backend.terminate = lambda *, sandbox_id: False  # type: ignore[assignment]
        self.backend.liveness_unavailable = True
        self.app.sandboxes.reap_expired(now=datetime(2999, 1, 1, tzinfo=UTC))
        self.assertEqual(self._row(uid)["status"], "cleanup_pending")

        # Still unreachable: the retry bumps the attempt and keeps the row.
        first = self.cleanup.retry_cleanup_pending(now=datetime(2999, 1, 1, tzinfo=UTC))
        self.assertEqual(first, {"ok": False, "pending": 1, "confirmed": 0, "retried": 1})
        self.assertEqual(self._row(uid)["phase"], "cleanup_attempt_2")
        self.assertEqual(len(self._sandbox_events("sandbox.cleanup_retried")), 1)

        # Provider comes back and confirms the delete.
        self.backend.terminate = original_terminate  # type: ignore[assignment]
        self.backend.liveness_unavailable = False
        second = self.cleanup.retry_cleanup_pending(
            now=datetime(2999, 1, 2, tzinfo=UTC)
        )
        self.assertEqual(
            second, {"ok": True, "pending": 0, "confirmed": 1, "retried": 0}
        )
        self.assertEqual(self._row(uid)["status"], "terminated")
        self.assertEqual(len(self._sandbox_events("sandbox.cleanup_confirmed")), 1)

    def test_the_retry_backs_off_before_asking_again(self) -> None:
        uid = "uid_backoff"
        self._running_row(sandbox_uid=uid, sandbox_id="sb-backoff")
        self.backend.terminate = lambda *, sandbox_id: False  # type: ignore[assignment]
        self.backend.liveness_unavailable = True
        self.app.sandboxes.reap_expired(now=datetime(2999, 1, 1, tzinfo=UTC))
        parked_at = parse_iso(self._row(uid)["updated_at"])
        assert parked_at is not None

        early = self.cleanup.retry_cleanup_pending(
            now=parked_at + timedelta(seconds=10)
        )
        self.assertEqual(early["retried"], 0)  # inside the first backoff window
        self.assertEqual(self._row(uid)["phase"], "cleanup_attempt_1")
        late = self.cleanup.retry_cleanup_pending(now=parked_at + timedelta(minutes=5))
        self.assertEqual(late["retried"], 1)
        self.assertEqual(self._row(uid)["phase"], "cleanup_attempt_2")

    def test_a_failed_provision_keeps_its_verdict_through_the_detour(self) -> None:
        # A wedged provision whose cleanup could not be confirmed parks, then
        # settles as `failed` (not a clean `terminated`) once the VM is gone.
        exp_id = self._experiment()
        uid = "uid_wedged_unconfirmed"
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid=uid,
            project_id=self.project_id,
            sandbox_id="sb-wedged-unconfirmed",
            status="provisioning",
            phase="connecting",
            provision_started_at="2026-01-01T00:00:00Z",
        )
        self.backend.alive["sb-wedged-unconfirmed"] = True
        original_terminate = self.backend.terminate
        self.backend.terminate = lambda *, sandbox_id: False  # type: ignore[assignment]
        self.backend.liveness_unavailable = True
        self.assertEqual(
            self.cleanup.sweep_stale_provisions(
                now=datetime(2026, 1, 1, 0, 20, tzinfo=UTC)
            ),
            0,
        )
        row = self._row(uid)
        self.assertEqual(row["status"], "cleanup_pending")
        self.assertIn("wedged past deadline", row["error"])

        self.backend.terminate = original_terminate  # type: ignore[assignment]
        self.backend.liveness_unavailable = False
        # The park stamp is wall-clock, so drive the retry from past it.
        self.cleanup.retry_cleanup_pending(now=datetime(2999, 1, 1, tzinfo=UTC))
        settled = self._row(uid)
        self.assertEqual(settled["status"], "failed")
        self.assertIn("wedged past deadline", settled["error"])

    def test_an_unreachable_lookup_never_terminalizes_an_unrecorded_row(self) -> None:
        # No sandbox_id: the deterministic-name probe is the only evidence, and
        # a provider that cannot be asked is not evidence the VM is gone.
        exp_id = self._experiment()
        uid = "uid_lookup_outage"
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid=uid,
            project_id=self.project_id,
            sandbox_id="",
            status="provisioning",
            phase="creating",
            provision_started_at="2026-01-01T00:00:00Z",
        )

        def exploding_find(*, experiment_id, sandbox_uid=""):
            raise RuntimeError("provider API timeout")

        self.backend.find_sandbox_id = exploding_find  # type: ignore[assignment]
        self.assertEqual(
            self.cleanup.sweep_stale_provisions(
                now=datetime(2026, 1, 1, 0, 20, tzinfo=UTC)
            ),
            0,
        )
        self.assertEqual(self._row(uid)["status"], "cleanup_pending")

    def test_an_authoritative_not_found_still_terminalizes(self) -> None:
        # Same row, but the provider answers and names nothing: that IS proof.
        exp_id = self._experiment()
        uid = "uid_lookup_empty"
        self.app.sandboxes.repository.upsert(
            experiment_id=exp_id,
            sandbox_uid=uid,
            project_id=self.project_id,
            sandbox_id="",
            status="provisioning",
            phase="creating",
            provision_started_at="2026-01-01T00:00:00Z",
        )
        self.assertEqual(
            self.cleanup.sweep_stale_provisions(
                now=datetime(2026, 1, 1, 0, 20, tzinfo=UTC)
            ),
            1,
        )
        self.assertEqual(self._row(uid)["status"], "failed")

    def test_a_pending_cleanup_makes_the_whole_pass_not_ok(self) -> None:
        uid = "uid_not_ok"
        self._running_row(sandbox_uid=uid, sandbox_id="sb-not-ok")
        self.backend.terminate = lambda *, sandbox_id: False  # type: ignore[assignment]
        self.backend.liveness_unavailable = True
        self.app.sandboxes.reap_expired(now=datetime(2999, 1, 1, tzinfo=UTC))
        report = self.cleanup.run_all(now=datetime(2999, 1, 1, tzinfo=UTC))
        self.assertEqual(report.cleanup_pending["pending"], 1)
        self.assertFalse(report.ok)

    def test_a_request_never_provisions_over_a_pending_cleanup(self) -> None:
        uid = "uid_no_clobber"
        exp_id = self._running_row(sandbox_uid=uid, sandbox_id="sb-no-clobber")
        self.backend.terminate = lambda *, sandbox_id: False  # type: ignore[assignment]
        self.backend.liveness_unavailable = True
        self.app.sandboxes.reap_expired(now=datetime(2999, 1, 1, tzinfo=UTC))
        self.backend.liveness_unavailable = False

        fresh = self.app.sandboxes.request(
            project_id=self.project_id,
            experiment_id=exp_id,
            public_key=DEFAULT_PUBLIC_KEY,
        )
        self.assertNotEqual(fresh["sandbox_uid"], uid)
        parked = self._row(uid)
        self.assertEqual(parked["status"], "cleanup_pending")
        self.assertEqual(parked["sandbox_id"], "sb-no-clobber")

    def test_a_failing_prune_is_reported_as_not_ok_not_as_zero(self) -> None:
        class ExplodingLedger:
            def prune(self, *, now=None):
                raise RuntimeError("ledger unreachable")

        cleanup = CleanupService(
            sandboxes=self.app.sandboxes,
            blobs=self.app.blobs,
            tool_call_ledger=ExplodingLedger(),
        )
        report = cleanup.run_all(now=datetime.now(tz=UTC))
        self.assertEqual(
            report.tool_calls_pruned,
            {"deleted": 0, "ok": False, "error": "ledger unreachable"},
        )
        # The rest of the pass still ran.
        self.assertEqual(report.orphan_vms_reaped, 0)


if __name__ == "__main__":
    unittest.main()

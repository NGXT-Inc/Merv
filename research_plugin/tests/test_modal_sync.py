"""Unit tests for the modal sync subsystem.

These exercise the three-way differ, scanner exclusions, baseline store, and
the engine's locking/skip-if-busy semantics. End-to-end backend integration is
covered by test_modal_backend.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.sync_config import SyncExclusionPolicy, normalize_sync_exclusions
from backend.execution.backends.modal.sync.baseline import BaselineStore
from backend.execution.backends.modal.sync.differ import three_way_diff
from backend.execution.backends.modal.sync.engine import (
    MAX_PASS_FILE_DETAILS,
    SyncEngine,
    _pass_file_details,
)
from backend.execution.backends.modal.sync.lock import InterProcessSyncLock
from backend.execution.backends.modal.sync.poller import SyncPoller
from backend.execution.backends.modal.sync.scanner import local_scan
from backend.execution.backends.modal.sync.types import (
    ConflictRecord,
    FileFingerprint,
    SyncPlan,
    SyncResult,
)


def fp(path: str, mtime: int, size: int) -> FileFingerprint:
    return FileFingerprint(path=path, mtime_ns=mtime, size_bytes=size)


class ThreeWayDiffTest(unittest.TestCase):
    def test_local_changed_only_pushes(self) -> None:
        local = {"a.txt": fp("a.txt", 2, 10)}
        remote = {"a.txt": fp("a.txt", 1, 10)}
        baseline = {"a.txt": (fp("a.txt", 1, 10), fp("a.txt", 1, 10))}
        plan = three_way_diff(local=local, remote=remote, baseline=baseline)
        self.assertEqual(len(plan.push), 1)
        self.assertEqual(plan.push[0].path, "a.txt")
        self.assertEqual(plan.pull, ())
        self.assertEqual(plan.conflicts, ())

    def test_remote_changed_only_pulls(self) -> None:
        local = {"a.txt": fp("a.txt", 1, 10)}
        remote = {"a.txt": fp("a.txt", 9, 99)}
        baseline = {"a.txt": (fp("a.txt", 1, 10), fp("a.txt", 1, 10))}
        plan = three_way_diff(local=local, remote=remote, baseline=baseline)
        self.assertEqual(len(plan.pull), 1)
        self.assertEqual(plan.pull[0].path, "a.txt")
        self.assertEqual(plan.push, ())

    def test_new_local_file_pushes(self) -> None:
        local = {"new.txt": fp("new.txt", 5, 5)}
        remote: dict[str, FileFingerprint] = {}
        baseline: dict[str, tuple] = {}
        plan = three_way_diff(local=local, remote=remote, baseline=baseline)
        self.assertEqual([f.path for f in plan.push], ["new.txt"])

    def test_new_remote_file_pulls(self) -> None:
        remote = {"new.txt": fp("new.txt", 5, 5)}
        local: dict[str, FileFingerprint] = {}
        baseline: dict[str, tuple] = {}
        plan = three_way_diff(local=local, remote=remote, baseline=baseline)
        self.assertEqual([f.path for f in plan.pull], ["new.txt"])

    def test_local_only_deletion_deletes_remote(self) -> None:
        local: dict[str, FileFingerprint] = {}
        remote = {"x.txt": fp("x.txt", 1, 1)}
        baseline = {"x.txt": (fp("x.txt", 1, 1), fp("x.txt", 1, 1))}
        plan = three_way_diff(local=local, remote=remote, baseline=baseline)
        self.assertEqual(plan.delete_remote, ("x.txt",))
        self.assertEqual(plan.delete_local, ())

    def test_both_sides_changed_marks_conflict(self) -> None:
        local = {"a.txt": fp("a.txt", 9, 99)}
        remote = {"a.txt": fp("a.txt", 7, 77)}
        baseline = {"a.txt": (fp("a.txt", 1, 10), fp("a.txt", 1, 10))}
        plan = three_way_diff(local=local, remote=remote, baseline=baseline)
        self.assertEqual(plan.push, ())
        self.assertEqual(plan.pull, ())
        self.assertEqual(len(plan.conflicts), 1)
        record: ConflictRecord = plan.conflicts[0]
        self.assertEqual(record.path, "a.txt")
        self.assertEqual(record.local, fp("a.txt", 9, 99))
        self.assertEqual(record.remote, fp("a.txt", 7, 77))

    def test_known_conflicts_are_skipped(self) -> None:
        local = {"a.txt": fp("a.txt", 9, 99)}
        remote = {"a.txt": fp("a.txt", 7, 77)}
        baseline = {"a.txt": (fp("a.txt", 1, 10), fp("a.txt", 1, 10))}
        plan = three_way_diff(
            local=local,
            remote=remote,
            baseline=baseline,
            conflict_paths={"a.txt"},
        )
        self.assertTrue(plan.is_empty())

    def test_unchanged_path_is_skipped(self) -> None:
        local = {"a.txt": fp("a.txt", 1, 10)}
        remote = {"a.txt": fp("a.txt", 1, 10)}
        baseline = {"a.txt": (fp("a.txt", 1, 10), fp("a.txt", 1, 10))}
        plan = three_way_diff(local=local, remote=remote, baseline=baseline)
        self.assertTrue(plan.is_empty())


class LocalScanExclusionsTest(unittest.TestCase):
    def test_scan_excludes_state_git_venv_pycache_and_pyc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "src").mkdir()
            (repo / "src" / "main.py").write_text("ok\n")
            (repo / "src" / "cached.pyc").write_text("bytecode\n")
            (repo / "__pycache__").mkdir()
            (repo / "__pycache__" / "a.pyc").write_text("x\n")
            (repo / ".git").mkdir()
            (repo / ".git" / "HEAD").write_text("ref\n")
            (repo / ".venv").mkdir()
            (repo / ".venv" / "python").write_text("\n")
            (repo / ".research_plugin").mkdir()
            (repo / ".research_plugin" / "state.sqlite").write_text("\n")
            (repo / ".research_plugin_job").mkdir()
            (repo / ".research_plugin_job" / "status.json").write_text("{}\n")

            result = local_scan(repo_root=repo)
            self.assertIn("src/main.py", result)
            self.assertNotIn("src/cached.pyc", result)
            self.assertFalse(any(p.startswith("__pycache__/") for p in result))
            self.assertFalse(any(p.startswith(".git/") for p in result))
            self.assertFalse(any(p.startswith(".venv/") for p in result))
            self.assertFalse(any(p.startswith(".research_plugin/") for p in result))
            self.assertFalse(any(p.startswith(".research_plugin_job/") for p in result))

    def test_scan_uses_custom_exclusion_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".venv").mkdir()
            (repo / ".venv" / "python").write_text("sync me\n")
            (repo / "tmp").mkdir()
            (repo / "tmp" / "x.txt").write_text("skip me\n")
            (repo / "src").mkdir()
            (repo / "src" / "main.pyc").write_text("sync bytecode\n")

            result = local_scan(
                repo_root=repo,
                exclusions=SyncExclusionPolicy(
                    names=("tmp",),
                    suffixes=(),
                    prefixes=(),
                ),
            )

            self.assertIn(".venv/python", result)
            self.assertIn("src/main.pyc", result)
            self.assertNotIn("tmp/x.txt", result)

    def test_config_accepts_paths_alias_for_prefixes(self) -> None:
        config = normalize_sync_exclusions({"paths": ["local/data"]})
        self.assertEqual(config["prefixes"], ["local/data"])


class BaselineStoreTest(unittest.TestCase):
    def test_upsert_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BaselineStore(db_path=Path(tmp) / "sync.sqlite")
            store.upsert_clean(
                project_id="p1",
                path="a.txt",
                local=fp("a.txt", 1, 10),
                remote=fp("a.txt", 2, 10),
                synced_at="2026-01-01T00:00:00Z",
            )
            baseline = store.load_baseline(project_id="p1")
            self.assertEqual(
                baseline["a.txt"],
                (fp("a.txt", 1, 10), fp("a.txt", 2, 10)),
            )

    def test_conflicts_are_excluded_from_baseline_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BaselineStore(db_path=Path(tmp) / "sync.sqlite")
            store.upsert_clean(
                project_id="p1",
                path="ok.txt",
                local=fp("ok.txt", 1, 1),
                remote=fp("ok.txt", 1, 1),
                synced_at="2026-01-01T00:00:00Z",
            )
            store.mark_conflict(
                project_id="p1",
                path="bad.txt",
                local=fp("bad.txt", 5, 5),
                remote=fp("bad.txt", 6, 6),
                when="2026-01-01T00:00:01Z",
            )
            baseline = store.load_baseline(project_id="p1")
            self.assertIn("ok.txt", baseline)
            self.assertNotIn("bad.txt", baseline)
            self.assertEqual(store.conflict_paths(project_id="p1"), {"bad.txt"})

    def test_totals_sums_clean_rows_and_excludes_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BaselineStore(db_path=Path(tmp) / "sync.sqlite")
            self.assertEqual(
                store.totals(project_id="p1"),
                {"files": 0, "local_bytes": 0, "remote_bytes": 0},
            )
            store.upsert_clean(
                project_id="p1", path="a.txt",
                local=fp("a.txt", 1, 10), remote=fp("a.txt", 2, 10),
                synced_at="2026-01-01T00:00:00Z",
            )
            store.upsert_clean(
                project_id="p1", path="b.bin",
                local=fp("b.bin", 1, 1000), remote=fp("b.bin", 2, 1000),
                synced_at="2026-01-01T00:00:00Z",
            )
            # A conflict row must not count toward the project total.
            store.mark_conflict(
                project_id="p1", path="bad.txt",
                local=fp("bad.txt", 5, 99), remote=fp("bad.txt", 6, 99),
                when="2026-01-01T00:00:01Z",
            )
            # Another project's rows must not leak in.
            store.upsert_clean(
                project_id="p2", path="c.txt",
                local=fp("c.txt", 1, 7), remote=fp("c.txt", 2, 7),
                synced_at="2026-01-01T00:00:00Z",
            )
            self.assertEqual(
                store.totals(project_id="p1"),
                {"files": 2, "local_bytes": 1010, "remote_bytes": 1010},
            )

    def test_known_projects_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BaselineStore(db_path=Path(tmp) / "sync.sqlite")
            self.assertEqual(store.known_projects(), [])
            store.register_project(
                project_id="proj_a",
                volume_name="research-plugin-proj_a",
                mount_path="/workspace/repo",
                repo_dir="",
                registered_at="2026-01-01T00:00:00Z",
            )
            self.assertEqual(store.known_projects(), ["proj_a"])
            info = store.project_info(project_id="proj_a")
            assert info is not None
            self.assertEqual(info["volume_name"], "research-plugin-proj_a")


class _StubVolume:
    """Minimal modal-volume stub that satisfies SyncEngine + scanner."""

    def listdir(self, _path: str, recursive: bool = True):  # noqa: ARG002
        return []


class SyncEngineLockingTest(unittest.TestCase):
    """Concurrency semantics of SyncEngine.sync.

    Strategy: install a hold-the-lock-then-release artifact by patching the
    engine's applier so we can pause a 'first' sync mid-flight from another
    thread, then assert how a 'second' sync behaves (queues vs. skips).
    """

    def _engine(self, tmp: Path) -> SyncEngine:
        repo = tmp / "repo"
        repo.mkdir()
        baseline = BaselineStore(db_path=tmp / "sync.sqlite")
        volume = _StubVolume()
        return SyncEngine(
            repo_root=repo,
            baseline=baseline,
            volume_provider=lambda _name: volume,
        )

    def _pause_applier(self, engine: SyncEngine) -> threading.Event:
        """Patch engine.applier.apply to block on this Event before returning."""
        gate = threading.Event()
        original_apply = engine.applier.apply

        def slow_apply(*args, **kwargs):
            result = original_apply(*args, **kwargs)
            gate.wait(timeout=5.0)
            return result

        engine.applier.apply = slow_apply  # type: ignore[assignment]
        return gate

    def test_skip_if_busy_skips_only_when_both_slots_full(self) -> None:
        """skip_if_busy=True returns immediately ONLY when running+queued
        are both occupied. With only the running slot taken, the caller
        should take the queued slot and wait its turn (no skip)."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            gate = self._pause_applier(engine)

            errors: list[BaseException] = []
            sync1_entered = threading.Event()
            sync2_entered = threading.Event()

            def sync1() -> None:
                try:
                    sync1_entered.set()
                    engine.sync(project_id="p1")
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            def sync2() -> None:
                # Default caller — takes the queued slot, waits, then runs.
                try:
                    sync2_entered.set()
                    engine.sync(project_id="p1")
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            t1 = threading.Thread(target=sync1, daemon=True)
            t1.start()
            self.assertTrue(sync1_entered.wait(timeout=2.0))
            time.sleep(0.05)  # let sync1 reach apply() and block on the gate

            t2 = threading.Thread(target=sync2, daemon=True)
            t2.start()
            self.assertTrue(sync2_entered.wait(timeout=2.0))
            time.sleep(0.05)  # let sync2 take the queued slot and block

            # Now: sync1 holds running, sync2 holds queued. Both slots full.
            # A third caller with skip_if_busy=True must return immediately.
            start = time.monotonic()
            result = engine.sync(
                project_id="p1",
                skip_if_busy=True,
            )
            elapsed = time.monotonic() - start

            self.assertTrue(result.skipped_busy)
            self.assertFalse(result.coalesced)
            self.assertLess(elapsed, 0.5)

            gate.set()
            t1.join(timeout=5.0)
            t2.join(timeout=5.0)
            self.assertEqual(errors, [])

    def test_default_caller_coalesces_when_both_slots_full(self) -> None:
        """When both slots are full, a default-policy caller waits for the
        already-queued sync to complete and returns coalesced=True."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))
            gate = self._pause_applier(engine)

            errors: list[BaseException] = []
            sync1_entered = threading.Event()
            sync2_entered = threading.Event()

            def sync1() -> None:
                try:
                    sync1_entered.set()
                    engine.sync(project_id="p1")
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            def sync2() -> None:
                try:
                    sync2_entered.set()
                    engine.sync(project_id="p1")
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            t1 = threading.Thread(target=sync1, daemon=True)
            t1.start()
            self.assertTrue(sync1_entered.wait(timeout=2.0))
            time.sleep(0.05)
            t2 = threading.Thread(target=sync2, daemon=True)
            t2.start()
            self.assertTrue(sync2_entered.wait(timeout=2.0))
            time.sleep(0.05)

            # Both slots full. Default caller should coalesce: wait for the
            # queued sync to finish, then return without running its own work.
            coalesce_result: list = []

            def sync3() -> None:
                coalesce_result.append(
                    engine.sync(project_id="p1")
                )

            t3 = threading.Thread(target=sync3, daemon=True)
            t3.start()
            # Verify sync3 is genuinely blocked while the queue is full.
            time.sleep(0.1)
            self.assertTrue(t3.is_alive())

            gate.set()
            t1.join(timeout=5.0)
            t2.join(timeout=5.0)
            t3.join(timeout=5.0)
            self.assertEqual(errors, [])
            self.assertEqual(len(coalesce_result), 1)
            self.assertTrue(coalesce_result[0].coalesced)
            self.assertFalse(coalesce_result[0].skipped_busy)

    def test_at_most_one_caller_queued_among_many_default_callers(self) -> None:
        """Five concurrent default-policy callers → exactly two actually run
        the underlying work (running + queued); the rest coalesce."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(Path(tmp))

            work_calls: list[str] = []
            work_lock = threading.Lock()
            first_entered = threading.Event()
            release = threading.Event()
            original_do_work = engine._do_sync_work  # type: ignore[attr-defined]

            def fake_work(*, project_id: str):
                with work_lock:
                    work_calls.append(project_id)
                    is_first = len(work_calls) == 1
                if is_first:
                    first_entered.set()
                    release.wait(timeout=5.0)
                return original_do_work(project_id=project_id)

            engine._do_sync_work = fake_work  # type: ignore[assignment]

            threads = [
                threading.Thread(
                    target=lambda: engine.sync(project_id="p1"),
                    daemon=True,
                )
                for _ in range(5)
            ]
            for t in threads:
                t.start()

            self.assertTrue(first_entered.wait(timeout=2.0))
            # Give the other 4 callers time to arrive at the queue check.
            time.sleep(0.2)

            release.set()
            for t in threads:
                t.join(timeout=5.0)
                self.assertFalse(t.is_alive())

            # The queue bound: max one running + one queued = 2 work executions.
            self.assertEqual(len(work_calls), 2)

    def test_skip_if_busy_skips_when_repo_lock_is_busy_for_other_project(self) -> None:
        """The repo-wide lock prevents cross-project sync passes from racing."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            baseline = BaselineStore(db_path=tmp_path / "sync.sqlite")

            gate = threading.Event()
            a_volume_requested = threading.Event()

            def volume_provider(name: str) -> _StubVolume:
                # Pause only when proj_a is being processed; proj_b sails by.
                if name.endswith("-proj_a"):
                    a_volume_requested.set()
                    gate.wait(timeout=5.0)
                return _StubVolume()

            engine = SyncEngine(
                repo_root=repo,
                baseline=baseline,
                volume_provider=volume_provider,
            )

            def first() -> None:
                engine.sync(project_id="proj_a")

            t = threading.Thread(target=first, daemon=True)
            t.start()
            self.assertTrue(a_volume_requested.wait(timeout=2.0))

            # proj_b takes its own project queue slot, but the repo-wide sync
            # lock is held by proj_a, so poller-style callers skip.
            start = time.monotonic()
            result_b = engine.sync(
                project_id="proj_b",
                skip_if_busy=True,
            )
            elapsed = time.monotonic() - start
            self.assertTrue(result_b.skipped_busy)
            self.assertLess(elapsed, 0.5)

            gate.set()
            t.join(timeout=5.0)
            self.assertFalse(t.is_alive())


class InterProcessSyncLockTest(unittest.TestCase):
    def test_nonblocking_acquire_fails_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".research_plugin" / "modal" / "sync.lock"
            lock = InterProcessSyncLock(lock_path=lock_path)
            with lock.acquire(blocking=True) as acquired:
                self.assertTrue(acquired)
                self.assertEqual(_child_lock_attempt(lock_path=lock_path), "busy")

            self.assertEqual(_child_lock_attempt(lock_path=lock_path), "acquired")


class SyncPollerGateTest(unittest.TestCase):
    def test_tick_skips_project_when_gate_blocks(self) -> None:
        baseline = _PollerBaseline(["proj_1"])
        engine = _RecordingPollEngine()
        events: list[tuple[str, dict]] = []
        poller = SyncPoller(
            engine=engine,  # type: ignore[arg-type]
            baseline=baseline,  # type: ignore[arg-type]
            should_sync_project=lambda project_id: project_id != "proj_1",
            activity=lambda event_type, payload: events.append((event_type, payload)),
        )

        poller._tick()

        self.assertEqual(engine.sync_calls, [])
        self.assertEqual(baseline.polled_projects, ["proj_1"])
        self.assertEqual(events, [("modal.sync.skipped_project_gate", {"project_id": "proj_1"})])


class _PollerBaseline:
    def __init__(self, project_ids: list[str]) -> None:
        self.project_ids = project_ids
        self.polled_projects: list[str] = []

    def known_projects(self) -> list[str]:
        return list(self.project_ids)

    def mark_polled(self, *, project_id: str, when: str) -> None:  # noqa: ARG002
        self.polled_projects.append(project_id)


class _RecordingPollEngine:
    def __init__(self) -> None:
        self.sync_calls: list[tuple[str, bool]] = []

    def sync(self, *, project_id: str, skip_if_busy: bool = False) -> SyncResult:
        self.sync_calls.append((project_id, skip_if_busy))
        return SyncResult(project_id=project_id)


def _child_lock_attempt(*, lock_path: Path) -> str:
    mcp_path = Path(__file__).resolve().parents[1] / "mcp_server"
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(mcp_path)
        if not existing_pythonpath
        else f"{mcp_path}{os.pathsep}{existing_pythonpath}"
    )
    script = """
import sys
from pathlib import Path
from backend.execution.backends.modal.sync.lock import InterProcessSyncLock

lock = InterProcessSyncLock(lock_path=Path(sys.argv[1]))
with lock.acquire(blocking=False) as acquired:
    print("acquired" if acquired else "busy")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script, str(lock_path)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout.strip()


class PassFileDetailsTest(unittest.TestCase):
    """The per-file breakdown embedded in modal.sync.pass events."""

    def test_labels_directions_and_counts_total(self) -> None:
        plan = SyncPlan(
            push=(fp("a.txt", 1, 10), fp("b.txt", 1, 30)),
            pull=(fp("c.txt", 1, 20),),
            delete_remote=("gone_remote.txt",),
            delete_local=("gone_local.txt",),
        )
        entries, total = _pass_file_details(plan, cap=80)
        self.assertEqual(total, 5)
        by_path = {e["path"]: e for e in entries}
        self.assertEqual(by_path["a.txt"]["dir"], "push")
        self.assertEqual(by_path["a.txt"]["size"], 10)
        self.assertEqual(by_path["c.txt"]["dir"], "pull")
        self.assertEqual(by_path["gone_remote.txt"]["dir"], "del_remote")
        self.assertEqual(by_path["gone_local.txt"]["dir"], "del_local")

    def test_sorts_largest_first_with_deletes_last(self) -> None:
        plan = SyncPlan(
            push=(fp("small.txt", 1, 5), fp("big.txt", 1, 500)),
            pull=(fp("mid.txt", 1, 50),),
            delete_remote=("zzz.txt",),
        )
        entries, _ = _pass_file_details(plan, cap=80)
        self.assertEqual([e["path"] for e in entries], ["big.txt", "mid.txt", "small.txt", "zzz.txt"])

    def test_caps_entries_but_reports_full_total(self) -> None:
        plan = SyncPlan(push=tuple(fp(f"f{i}.txt", 1, i) for i in range(10)))
        entries, total = _pass_file_details(plan, cap=3)
        self.assertEqual(len(entries), 3)
        self.assertEqual(total, 10)
        # Largest-first under the cap: sizes 9, 8, 7.
        self.assertEqual([e["size"] for e in entries], [9, 8, 7])


class _PushFakeVolume:
    """Modal-volume stub that records pushes and reflects them in listdir, so a
    push-only sync runs end-to-end and the post-apply remote scan sees the
    uploaded files — as a real Volume would. The pre-apply scan still sees an
    empty remote (nothing uploaded yet), so the push is still planned."""

    def __init__(self) -> None:
        self.uploaded: list[tuple[str, str]] = []

    def listdir(self, _path: str, recursive: bool = True):  # noqa: ARG002
        return [
            SimpleNamespace(path=remote, size=os.path.getsize(local), mtime=0)
            for local, remote in self.uploaded
        ]

    def batch_upload(self, force: bool = False):  # noqa: ARG002
        volume = self

        class _Batch:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def put_file(self_inner, local: str, remote: str) -> None:
                volume.uploaded.append((local, remote))

        return _Batch()


class PassEventDetailEmissionTest(unittest.TestCase):
    """A real bidirectional pass emits the enriched modal.sync.pass payload."""

    def test_modal_sync_pass_carries_files_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo = tmp / "repo"
            repo.mkdir()
            (repo / "a.txt").write_text("hello world", encoding="utf-8")  # 11 bytes
            baseline = BaselineStore(db_path=tmp / "sync.sqlite")
            volume = _PushFakeVolume()
            events: list[tuple[str, dict]] = []
            engine = SyncEngine(
                repo_root=repo,
                baseline=baseline,
                volume_provider=lambda _name: volume,
                activity=lambda event_type, payload: events.append((event_type, payload)),
            )

            engine.sync(project_id="p1")

            passes = [payload for name, payload in events if name == "modal.sync.pass"]
            self.assertEqual(len(passes), 1)
            payload = passes[0]
            self.assertEqual(payload["pushed"], 1)
            self.assertEqual(payload["bytes_pushed"], 11)
            self.assertEqual(payload["bytes_pulled"], 0)
            self.assertEqual(payload["files_total"], 1)
            self.assertFalse(payload["files_truncated"])
            self.assertEqual(
                payload["files"],
                [{"path": "a.txt", "dir": "push", "size": 11}],
            )
            # Whole-project totals after the pass: the pushed file is now in
            # sync on both sides, so local and remote each total its 11 bytes.
            self.assertEqual(payload["total_files"], 1)
            self.assertEqual(payload["total_bytes"], 11)
            self.assertEqual(payload["total_remote_bytes"], 11)
            # One push, to the repo-relative remote path. (Compare basename +
            # remote only: the engine resolves repo_root, so on macOS the local
            # path gains a /private prefix vs the tmpdir we created.)
            self.assertEqual(len(volume.uploaded), 1)
            local_path, remote_path = volume.uploaded[0]
            self.assertTrue(local_path.endswith("/repo/a.txt"))
            self.assertEqual(remote_path, "a.txt")

    def test_cap_constant_is_positive(self) -> None:
        self.assertGreater(MAX_PASS_FILE_DETAILS, 0)


if __name__ == "__main__":
    unittest.main()

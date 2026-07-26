"""Migration 37 and the durable tool-call ledger it creates.

Two halves: the ladder (the table and every index arrive on a fresh database
AND on one that predates them) and the writer (a call becomes a row of sizes,
digests, and outcomes — never payloads — and a broken ledger never breaks the
call it was observing).
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from merv.brain.kernel.request_context import begin_request, bind_principal, reset_request
from merv.brain.kernel.state.store import (
    MIGRATIONS,
    SCHEMA,
    TOOL_CALL_LEDGER_INDEXES,
    StateStore,
)
from merv.brain.kernel.state.tool_call_ledger import (
    DEFAULT_RETENTION_DAYS,
    TOOL_CALL_RETENTION_DAYS_ENV_VAR,
    ToolCallLedger,
)

LEDGER_INDEX_NAMES = frozenset(
    statement.split("IF NOT EXISTS ")[1].split()[0]
    for statement in TOOL_CALL_LEDGER_INDEXES
)


def _schema_without_tool_calls() -> str:
    """SCHEMA as it stood before migration 37: no tool_calls table at all."""
    return ";".join(
        block
        for block in SCHEMA.split(";")
        if "CREATE TABLE IF NOT EXISTS tool_calls" not in block
    )


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }


class Migration37Test(unittest.TestCase):
    def test_fresh_database_gets_the_table_and_every_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(db_path=Path(tmp) / "state.sqlite")
            with store.transaction() as conn:
                columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(tool_calls)").fetchall()
                }
                self.assertEqual(
                    columns,
                    {
                        "id", "ts", "request_id", "principal_id", "tool", "source",
                        "project_id", "target_type", "target_id", "status",
                        "error_code", "error_head", "duration_ms", "sent_chars",
                        "received_chars", "args_digest",
                    },
                )
                self.assertLessEqual(LEDGER_INDEX_NAMES, _indexes(conn))
                applied = {
                    int(row["version"])
                    for row in conn.execute(
                        "SELECT version FROM schema_migrations"
                    ).fetchall()
                }
                self.assertIn(37, applied)

    def test_database_that_predates_the_ledger_converges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.sqlite"
            conn = sqlite3.connect(db_path)
            conn.executescript(_schema_without_tool_calls())
            for version, name, _ in MIGRATIONS:
                if version < 37:
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations "
                        "(version, name, applied_at) VALUES (?, ?, '2026-01-01T00:00:00Z')",
                        (version, name),
                    )
            conn.commit()
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertNotIn("tool_calls", tables, "fixture must start without it")
            self.assertFalse(LEDGER_INDEX_NAMES & _indexes(conn))
            conn.close()

            StateStore(db_path=db_path)

            conn = sqlite3.connect(db_path)
            try:
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                self.assertIn("tool_calls", tables)
                # Every index, including the ones on the pre-existing tables.
                self.assertLessEqual(LEDGER_INDEX_NAMES, _indexes(conn))
            finally:
                conn.close()

    def test_schema_declares_no_index_at_all(self) -> None:
        """The migration-36 outage in general form: SCHEMA runs before the
        ladder, so migration 37's indexes may only live in the migration."""
        for name in LEDGER_INDEX_NAMES:
            self.assertNotIn(name, SCHEMA)

    def test_reapplying_the_migration_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.sqlite"
            store = StateStore(db_path=db_path)
            with store.transaction() as conn:
                conn.execute("DELETE FROM schema_migrations WHERE version = 37")
            StateStore(db_path=db_path)  # boots without raising


class ToolCallLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(db_path=Path(self.tmp.name) / "state.sqlite")
        self.ledger = ToolCallLedger(store=self.store, env={})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _rows(self) -> list[dict[str, object]]:
        with self.store.transaction() as conn:
            return [
                {key: row[key] for key in row.keys()}
                for row in conn.execute(
                    "SELECT * FROM tool_calls ORDER BY id"
                ).fetchall()
            ]

    def test_ok_call_records_sizes_digest_and_correlation(self) -> None:
        scope = begin_request(request_id="req-abc")
        bind_principal(principal_id="key:pk_1")
        try:
            self.ledger.record(
                tool="experiment.get_state",
                source="mcp",
                status="ok",
                duration_ms=17,
                arguments={"project_id": "proj_1", "experiment_id": "exp_1"},
                result={"status": "running"},
            )
        finally:
            reset_request(scope)
        (row,) = self._rows()
        self.assertEqual(row["request_id"], "req-abc")
        self.assertEqual(row["principal_id"], "key:pk_1")
        self.assertEqual(row["tool"], "experiment.get_state")
        self.assertEqual(row["source"], "mcp")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["project_id"], "proj_1")
        self.assertEqual((row["target_type"], row["target_id"]), ("experiment", "exp_1"))
        self.assertEqual(row["duration_ms"], 17)
        self.assertGreater(int(row["sent_chars"]), 0)
        self.assertEqual(row["received_chars"], len('{"status": "running"}'))
        self.assertEqual(len(str(row["args_digest"])), 16)
        self.assertEqual(row["error_head"], "")

    def test_error_call_records_one_scrubbed_capped_line(self) -> None:
        self.ledger.record(
            tool="review.submit",
            source="mcp",
            status="error",
            duration_ms=3,
            arguments={"project_id": "proj_1"},
            error="gate refused: " + "x" * 500 + "\nstack frame that never lands",
            error_code="review_gate",
        )
        (row,) = self._rows()
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error_code"], "review_gate")
        head = str(row["error_head"])
        self.assertEqual(len(head), 200)
        self.assertTrue(head.startswith("gate refused: "))
        self.assertNotIn("stack frame", head)

    def test_secrets_never_reach_the_row_or_its_digest(self) -> None:
        secret = {"project_id": "proj_1", "reviewer_capability": "rp_supersecret"}
        self.ledger.record(tool="review.start", source="mcp", arguments=secret)
        redacted = dict(secret, reviewer_capability="[redacted]")
        self.ledger.record(tool="review.start", source="mcp", arguments=redacted)
        first, second = self._rows()
        self.assertNotIn("rp_supersecret", str(first))
        # Redaction happens before the hash, so the capability cannot be
        # brute-forced back out of the digest either.
        self.assertEqual(first["args_digest"], second["args_digest"])

    def test_rejection_is_its_own_status(self) -> None:
        self.ledger.reject(
            source="http", error_code="project_scope_forbidden", error="wrong project"
        )
        (row,) = self._rows()
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["tool"], "")
        self.assertEqual(row["error_head"], "wrong project")

    def test_a_broken_ledger_counts_the_drop_and_raises_nothing(self) -> None:
        dropped: list[str] = []

        class BrokenStore:
            def connect(self):
                raise sqlite3.OperationalError("database is locked")

        ledger = ToolCallLedger(
            store=BrokenStore(), env={}, on_failure=dropped.append
        )
        ledger.record(tool="claim.list", source="mcp", arguments={})
        self.assertEqual(ledger.failures, 1)
        self.assertEqual(dropped, ["database is locked"])

    def test_prune_deletes_only_expired_rows_and_reports_honestly(self) -> None:
        now = datetime.now(tz=UTC)
        self.ledger.record(tool="claim.list", source="mcp", arguments={})
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO tool_calls (ts, tool, source, status) VALUES (?, ?, ?, ?)",
                ("2020-01-01T00:00:00Z", "ancient", "mcp", "ok"),
            )
        outcome = self.ledger.prune(now=now)
        self.assertEqual(outcome["deleted"], 1)
        self.assertTrue(outcome["ok"])
        self.assertFalse(outcome["more"])
        self.assertEqual([row["tool"] for row in self._rows()], ["claim.list"])
        # A second pass finds nothing, and says so as a healthy zero.
        self.assertEqual(self.ledger.prune(now=now), {
            "deleted": 0, "ok": True, "cutoff": outcome["cutoff"], "more": False
        })

    def test_a_failed_prune_reports_not_ok_rather_than_zero(self) -> None:
        class BrokenStore:
            def connect(self):
                raise sqlite3.OperationalError("no such table: tool_calls")

        outcome = ToolCallLedger(store=BrokenStore(), env={}).prune()
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["deleted"], 0)
        self.assertIn("no such table", outcome["error"])

    def test_retention_is_env_overridable_and_never_collapses_to_zero(self) -> None:
        self.assertEqual(
            ToolCallLedger(store=self.store, env={}).retention_days,
            DEFAULT_RETENTION_DAYS,
        )
        self.assertEqual(
            ToolCallLedger(
                store=self.store, env={TOOL_CALL_RETENTION_DAYS_ENV_VAR: "7"}
            ).retention_days,
            7,
        )
        for hostile in ("0", "-5", "not-a-number"):
            with self.subTest(value=hostile):
                ledger = ToolCallLedger(
                    store=self.store, env={TOOL_CALL_RETENTION_DAYS_ENV_VAR: hostile}
                )
                self.assertGreaterEqual(ledger.retention_days, 1)

    def test_prune_keeps_rows_inside_the_horizon(self) -> None:
        self.ledger.record(tool="claim.list", source="mcp", arguments={})
        just_inside = datetime.now(tz=UTC) + timedelta(
            days=DEFAULT_RETENTION_DAYS - 1
        )
        self.assertEqual(self.ledger.prune(now=just_inside)["deleted"], 0)
        self.assertEqual(len(self._rows()), 1)


if __name__ == "__main__":
    unittest.main()

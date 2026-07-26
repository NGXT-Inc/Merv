"""Durable sandbox persistence.

`SandboxRepository` owns every read and write of the `sandboxes` table, the
active sandbox↔experiment attachment table, and the sandbox event stream. It
knows nothing about backends, threads, tunnels, or rsync — callers hand it row
dicts and field updates, and it never calls out. Terminal marks return the
row facts (sandbox_id/uid) so `SandboxLifecycle` — the sole caller allowed to
mark — can run teardown.
"""

from __future__ import annotations

from contextlib import closing
import json
import uuid
from typing import Any

from .sandbox_support import (
    ACTIVE_SANDBOX_STATUSES,
    CLEANUP_PENDING_STATUS,
    TERMINAL_SANDBOX_STATUSES,
    cleanup_attempt_phase,
)
from ..kernel.state.store import BaseStateStore, next_created_seq, row_to_dict
from ..kernel.utils import NotFoundError, ValidationError, new_id, now_iso


class SandboxRepository:
    """Owns sandbox-row persistence: upserts, scoping, status marks, events.

    Persistence only: terminal marks return the row facts (sandbox_id/uid) so
    the lifecycle layer — the sole caller allowed to mark — can run teardown;
    the repository itself never calls out.
    """

    def __init__(self, *, store: BaseStateStore) -> None:
        self.store = store

    def _row_dict(self, *, row: Any, conn: Any) -> dict[str, Any]:
        data = row_to_dict(row=row) or {}
        if data.get("experiment_id"):
            return data
        sandbox_uid = str(data.get("sandbox_uid") or "")
        if sandbox_uid:
            data["experiment_id"] = self._primary_experiment_id(
                conn=conn, sandbox_uid=sandbox_uid
            )
        else:
            data["experiment_id"] = ""
        return data

    def _primary_experiment_id(self, *, conn: Any, sandbox_uid: str) -> str:
        """Compatibility projection: first active attachment for a sandbox."""
        row = conn.execute(
            """
            SELECT experiment_id
            FROM sandbox_attachments
            WHERE sandbox_uid = ? AND detached_at IS NULL
            ORDER BY attached_at, experiment_id
            LIMIT 1
            """,
            (sandbox_uid,),
        ).fetchone()
        if row is not None and row["experiment_id"]:
            return str(row["experiment_id"])
        row = conn.execute(
            """
            SELECT experiment_id
            FROM sandbox_attachments
            WHERE sandbox_uid = ?
            ORDER BY attached_at DESC, experiment_id
            LIMIT 1
            """,
            (sandbox_uid,),
        ).fetchone()
        return (
            str(row["experiment_id"])
            if row is not None and row["experiment_id"]
            else ""
        )

    # ---------- reads ----------

    def load_row(self, *, experiment_id: str) -> dict[str, Any]:
        with closing(self.store.connect()) as conn:
            sandbox_uid = self._primary_uid(
                conn=conn, experiment_id=experiment_id
            ) or self._latest_uid(conn=conn, experiment_id=experiment_id)
            if sandbox_uid is None:
                raise NotFoundError(f"sandbox not found: {experiment_id}")
            row = conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_uid = ?", (sandbox_uid,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"sandbox not found: {experiment_id}")
            return self._row_dict(row=row, conn=conn)

    def get_by_uid(self, *, sandbox_uid: str) -> dict[str, Any]:
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_uid = ?", (sandbox_uid,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"sandbox not found: {sandbox_uid}")
            return self._row_dict(row=row, conn=conn)

    def list_by_experiment(self, *, experiment_id: str) -> list[dict[str, Any]]:
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                """
                SELECT s.*
                FROM sandboxes s
                JOIN sandbox_attachments a ON a.sandbox_uid = s.sandbox_uid
                WHERE a.experiment_id = ? AND a.detached_at IS NULL
                ORDER BY s.created_seq DESC
                """,
                (experiment_id,),
            ).fetchall()
            return [self._row_dict(row=row, conn=conn) for row in rows]

    def active_experiment_ids(self, *, sandbox_uid: str) -> list[str]:
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                """
                SELECT experiment_id
                FROM sandbox_attachments
                WHERE sandbox_uid = ? AND detached_at IS NULL
                ORDER BY attached_at, experiment_id
                """,
                (sandbox_uid,),
            ).fetchall()
            return [str(row["experiment_id"]) for row in rows]

    def tenant_for_project(self, *, project_id: str) -> str:
        """The owning tenant of a project (cloud plan Phase 7), 'local' default."""
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                "SELECT tenant_id FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return str(row["tenant_id"]) if row is not None else "local"

    def fetch_scoped(
        self,
        *,
        experiment_id: str | None,
        project_id: str | None,
        tenant_id: str | None = None,
        sandbox_uid: str | None = None,
    ) -> dict[str, Any]:
        with closing(self.store.connect()) as conn:
            if project_id is not None or tenant_id is not None:
                project_id = self.store.require_project_id(
                    conn=conn, project_id=project_id, tenant_id=tenant_id
                )
            target_uid = (sandbox_uid or "").strip()
            if target_uid:
                row = conn.execute(
                    "SELECT * FROM sandboxes WHERE sandbox_uid = ?", (target_uid,)
                ).fetchone()
            else:
                if not experiment_id:
                    raise NotFoundError("sandbox_uid or experiment_id is required")
                target_uid = (
                    self._primary_uid(conn=conn, experiment_id=experiment_id)
                    or self._latest_uid(conn=conn, experiment_id=experiment_id)
                    or ""
                )
                row = (
                    conn.execute(
                        "SELECT * FROM sandboxes WHERE sandbox_uid = ?", (target_uid,)
                    ).fetchone()
                    if target_uid
                    else None
                )
            if row is None:
                if target_uid:
                    raise NotFoundError(f"sandbox not found: {target_uid}")
                raise NotFoundError(f"no sandbox for experiment: {experiment_id}")
            if experiment_id:
                attached = conn.execute(
                    """
                    SELECT 1 FROM sandbox_attachments
                    WHERE sandbox_uid = ? AND experiment_id = ? AND detached_at IS NULL
                    LIMIT 1
                    """,
                    (row["sandbox_uid"], experiment_id),
                ).fetchone()
                if attached is None and str(row["status"]) in TERMINAL_SANDBOX_STATUSES:
                    # Going terminal is exactly what closes every attachment,
                    # so a dead box never has an open one — and refusing it here
                    # makes its final receipts unreadable at the one moment they
                    # matter: a caller naming both a terminal sandbox_uid and
                    # its experiment would be told "not found" rather than how
                    # the run ended. Match the HISTORICAL attachment instead of
                    # dropping the check, so the experiment binding still holds.
                    attached = conn.execute(
                        """
                        SELECT 1 FROM sandbox_attachments
                        WHERE sandbox_uid = ? AND experiment_id = ?
                        LIMIT 1
                        """,
                        (row["sandbox_uid"], experiment_id),
                    ).fetchone()
                if attached is None:
                    raise NotFoundError(f"no sandbox for experiment: {experiment_id}")
            if project_id is not None and row["project_id"] != project_id:
                raise NotFoundError(
                    f"sandbox not found in project {project_id}: {experiment_id}"
                )
            return self._row_dict(row=row, conn=conn)

    def exists(self, *, experiment_id: str) -> bool:
        with closing(self.store.connect()) as conn:
            return (
                conn.execute(
                    """
                    SELECT 1
                    FROM sandbox_attachments
                    WHERE experiment_id = ? AND detached_at IS NULL
                    """,
                    (experiment_id,),
                ).fetchone()
                is not None
            )

    def list_rows(self, *, project_id: str | None) -> list[dict[str, Any]]:
        with closing(self.store.connect()) as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            rows = conn.execute(
                "SELECT * FROM sandboxes WHERE project_id = ? ORDER BY created_seq DESC",
                (project_id,),
            ).fetchall()
            return [self._row_dict(row=row, conn=conn) for row in rows]

    def rows_for_experiment(
        self, *, conn: Any, project_id: str, experiment_id: str
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT s.*
            FROM sandboxes s
            JOIN sandbox_attachments a ON a.sandbox_uid = s.sandbox_uid
            WHERE a.experiment_id = ? AND s.project_id = ? AND a.detached_at IS NULL
            ORDER BY s.created_seq DESC
            """,
            (experiment_id, project_id),
        ).fetchall()
        return [
            {**(row_to_dict(row=row) or {}), "experiment_id": experiment_id}
            for row in rows
        ]

    def rows_for_project(self, *, conn: Any, project_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM sandboxes WHERE project_id = ? ORDER BY created_seq DESC",
            (project_id,),
        ).fetchall()
        return [row_to_dict(row=row) or {} for row in rows]

    def list_running_rows(self) -> list[dict[str, Any]]:
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM sandboxes WHERE status = 'running' ORDER BY created_seq DESC"
            ).fetchall()
            return [self._row_dict(row=row, conn=conn) for row in rows]

    def list_rows_by_status(self, *, status: str) -> list[dict[str, Any]]:
        """All sandbox rows (across tenants/projects) in ``status``.

        The cross-project read the cloud cleanup sweeps need: the orphan-VM and
        stale-provision reapers reconcile every running/provisioning row, not a
        single project's. Local mode (one project) gets the same rows it always
        did.
        """
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM sandboxes WHERE status = ? ORDER BY created_seq DESC",
                (status,),
            ).fetchall()
            return [self._row_dict(row=row, conn=conn) for row in rows]

    def _primary_uid(self, *, conn: Any, experiment_id: str) -> str | None:
        """Most recent running sandbox attached to the experiment."""
        statuses = tuple(ACTIVE_SANDBOX_STATUSES)
        if not statuses:
            return None
        placeholders = ", ".join("?" for _ in statuses)
        row = conn.execute(
            f"""
            SELECT s.sandbox_uid
            FROM sandboxes s
            JOIN sandbox_attachments a ON a.sandbox_uid = s.sandbox_uid
            WHERE a.experiment_id = ?
              AND a.detached_at IS NULL
              AND s.status IN ({placeholders})
            ORDER BY s.created_seq DESC
            LIMIT 1
            """,
            (experiment_id, *statuses),
        ).fetchone()
        return (
            str(row["sandbox_uid"]) if row is not None and row["sandbox_uid"] else None
        )

    def _latest_uid(self, *, conn: Any, experiment_id: str) -> str | None:
        """Newest non-terminal sandbox attached to the experiment.

        `cleanup_pending` is included on purpose: sandbox.get must still show a
        row whose VM may be alive. Only sandbox.request refuses to reuse it.
        """
        statuses = tuple(TERMINAL_SANDBOX_STATUSES)
        placeholders = ", ".join("?" for _ in statuses)
        row = conn.execute(
            f"""
            SELECT s.sandbox_uid
            FROM sandboxes s
            JOIN sandbox_attachments a ON a.sandbox_uid = s.sandbox_uid
            WHERE a.experiment_id = ?
              AND a.detached_at IS NULL
              AND s.status NOT IN ({placeholders})
            ORDER BY s.created_seq DESC
            LIMIT 1
            """,
            (experiment_id, *statuses),
        ).fetchone()
        return (
            str(row["sandbox_uid"]) if row is not None and row["sandbox_uid"] else None
        )

    def has_active_for_experiment(
        self, *, experiment_id: str, exclude_sandbox_uid: str | None = None
    ) -> bool:
        """Whether the experiment has another sandbox that may still exist.

        `cleanup_pending` counts. This guards the deterministic-name orphan
        sweep, and a parked sibling is precisely the row whose VM may still be
        up — one that answers to the same experiment-derived name. Leaving it
        out lets the broad lookup find and destroy it while cleaning up a
        different attempt (audit SAN-06).
        """
        statuses = tuple(
            {*ACTIVE_SANDBOX_STATUSES, "provisioning", CLEANUP_PENDING_STATUS}
        )
        if not statuses:
            return False
        placeholders = ", ".join("?" for _ in statuses)
        params: list[Any] = [experiment_id, *statuses]
        clause = ""
        exclude = (exclude_sandbox_uid or "").strip()
        if exclude:
            clause = "AND sandboxes.sandbox_uid != ?"
            params.append(exclude)
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                f"""
                SELECT 1 FROM sandboxes
                JOIN sandbox_attachments a ON a.sandbox_uid = sandboxes.sandbox_uid
                WHERE a.experiment_id = ?
                  AND a.detached_at IS NULL
                  AND sandboxes.status IN ({placeholders}) {clause}
                LIMIT 1
                """,
                params,
            ).fetchone()
            return row is not None

    # ---------- writes ----------

    def new_sandbox_uid(self) -> str:
        return uuid.uuid4().hex

    def _guarded_update(
        self,
        *,
        conn: Any,
        sandbox_uid: str,
        assignments: str,
        values: list[Any],
        expected_project_id: str,
        extra_clause: str = "",
        extra_values: list[Any] | None = None,
    ) -> int:
        """One uid-keyed sandbox UPDATE bound to the project that owns the row.

        Every runtime writer — heartbeat, lifetime, command snapshot, terminal
        mark, run mirror — names the project it believes it is writing for, and
        that name rides in the WHERE clause. A caller holding only a uid can
        therefore never reach another project's row (audit SAN-02). Returns the
        rowcount so callers can tell a refused write from a no-op.
        """
        row = conn.execute(
            "SELECT project_id FROM sandboxes WHERE sandbox_uid = ?", (sandbox_uid,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"sandbox not found: {sandbox_uid}")
        owner_clause, owner_values = _owner_guard(
            row=row, expected_project_id=expected_project_id, uid=sandbox_uid
        )
        cursor = conn.execute(
            f"UPDATE sandboxes SET {assignments} "
            f"WHERE sandbox_uid = ?{owner_clause}{extra_clause}",
            [*values, sandbox_uid, *owner_values, *(extra_values or [])],
        )
        return int(getattr(cursor, "rowcount", 0))

    def upsert(
        self,
        *,
        experiment_id: str,
        sandbox_uid: str,
        expected_project_id: str = "",
        **fields: Any,
    ) -> None:
        now = now_iso()
        expected = str(expected_project_id or "").strip()
        with self.store.transaction() as conn:
            target_uid = str(sandbox_uid or "").strip()
            if not target_uid:
                raise ValueError("sandbox_uid is required")
            exists = conn.execute(
                "SELECT sandbox_uid, project_id, tenant_id FROM sandboxes "
                "WHERE sandbox_uid = ?",
                (target_uid,),
            ).fetchone()
            payload = dict(fields)
            payload.pop("experiment_id", None)
            if expected:
                # A caller that names the project it believes it is writing for
                # authenticates itself: the name goes into the WHERE clause
                # below instead of being read back off the target row, so a
                # provision callback can never land on a row it never owned
                # (audit SAN-02).
                incoming = str(payload.get("project_id") or "")
                if incoming and incoming != expected:
                    raise ValidationError(
                        f"sandbox {target_uid}: expected project {expected} does "
                        f"not match the project being written ({incoming})",
                        details={"sandbox_uid": target_uid, "field": "project_id"},
                    )
                payload.setdefault("project_id", expected)
            if payload.get("project_id") and not payload.get("tenant_id"):
                tenant_row = conn.execute(
                    "SELECT tenant_id FROM projects WHERE id = ?",
                    (payload["project_id"],),
                ).fetchone()
                payload["tenant_id"] = (
                    str(tenant_row["tenant_id"]) if tenant_row is not None else "local"
                )
            payload["updated_at"] = now
            if exists is None:
                payload["sandbox_uid"] = target_uid
                payload.setdefault("created_at", now)
                # Insertion-order column (cloud plan Phase 6): replaces rowid
                # ordering for the most-recent-first sandbox listings.
                payload["created_seq"] = next_created_seq(conn=conn, table="sandboxes")
                columns = ", ".join(payload)
                placeholders = ", ".join("?" for _ in payload)
                conn.execute(
                    f"INSERT INTO sandboxes ({columns}) VALUES ({placeholders})",
                    list(payload.values()),
                )
                self._ensure_attachment(
                    conn=conn,
                    sandbox_uid=str(payload["sandbox_uid"]),
                    experiment_id=experiment_id,
                    attached_at=str(payload["created_at"]),
                )
            else:
                sandbox_uid = str(exists["sandbox_uid"] or target_uid)
                # Ownership is immutable, so it also guards the write: a row
                # that changed hands takes no update at all (audit SAN-02).
                owner_clause, owner_values = _owner_guard(
                    row=exists, expected_project_id=expected, uid=sandbox_uid
                )
                _reject_ownership_change(row=exists, payload=payload, uid=sandbox_uid)
                assignments = ", ".join(f"{key} = ?" for key in payload)
                cursor = conn.execute(
                    f"UPDATE sandboxes SET {assignments} "
                    f"WHERE sandbox_uid = ?{owner_clause}",
                    [*payload.values(), sandbox_uid, *owner_values],
                )
                if int(getattr(cursor, "rowcount", 0)) != 1:
                    raise NotFoundError(f"sandbox not found: {sandbox_uid}")
                if sandbox_uid and str(payload.get("status") or "") not in {
                    "",
                    "terminated",
                    "failed",
                }:
                    self._ensure_attachment(
                        conn=conn,
                        sandbox_uid=sandbox_uid,
                        experiment_id=experiment_id,
                        attached_at=now,
                    )

    def create_sandbox(
        self, *, experiment_id: str, expected_project_id: str = "", **fields: Any
    ) -> str:
        """Insert a distinct row for a parallel sandbox under the experiment."""
        sandbox_uid = str(fields.pop("sandbox_uid", "") or self.new_sandbox_uid())
        self.upsert(
            experiment_id=experiment_id,
            sandbox_uid=sandbox_uid,
            expected_project_id=expected_project_id,
            **fields,
        )
        return sandbox_uid

    def provision_additional(
        self, *, experiment_id: str, expected_project_id: str = "", **fields: Any
    ) -> str:
        return self.create_sandbox(
            experiment_id=experiment_id,
            expected_project_id=expected_project_id,
            **fields,
        )

    def attach(
        self,
        *,
        sandbox_uid: str,
        experiment_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """Add an active experiment association to a live sandbox row.

        Ownership is never rewritten here (audit SAN-02): attaching binds an
        experiment to a sandbox the caller's project already owns. A row owned
        by another project reads as not found rather than changing hands — an
        attach that rebinds project_id/tenant_id off a bare uid is exactly how
        another project's running VM (and its bill) would be handed over.
        """
        now = now_iso()
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_uid = ?", (sandbox_uid,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"sandbox not found: {sandbox_uid}")
            owner_clause, owner_values = _owner_guard(
                row=row, expected_project_id=project_id, uid=sandbox_uid
            )
            self._ensure_attachment(
                conn=conn,
                sandbox_uid=sandbox_uid,
                experiment_id=experiment_id,
                attached_at=now,
            )
            cursor = conn.execute(
                f"""
                UPDATE sandboxes
                SET phase = '', detail = '', error = '', updated_at = ?
                WHERE sandbox_uid = ?{owner_clause}
                """,
                (now, sandbox_uid, *owner_values),
            )
            if int(getattr(cursor, "rowcount", 0)) != 1:
                raise NotFoundError(f"sandbox not found: {sandbox_uid}")
            fresh = conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_uid = ?", (sandbox_uid,)
            ).fetchone()
            return self._row_dict(row=fresh, conn=conn)

    def record_generation(
        self,
        *,
        experiment_id: str,
        project_id: str,
        sandbox_id: str = "",
        provider: str = "",
        instance_type: str = "",
        gpu: str = "",
        price_usd_per_hour: float = 0.0,
        key_id: str = "",
    ) -> str:
        """Append a per-generation spend-ledger row (cloud plan Phase 7).

        The sandboxes row retains only its current generation, so historical
        spend cannot be reconstructed from it. Each provisioned generation lands
        here with its provider price quote and the tenant (reached through the
        project) so total spend is reconstructable. Always recorded; in local
        mode the 'local' tenant simply has no quota to govern it.
        """
        generation_id = new_id(prefix="sbg")
        now = now_iso()
        with self.store.transaction() as conn:
            tenant_row = conn.execute(
                "SELECT tenant_id FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            tenant_id = (
                str(tenant_row["tenant_id"]) if tenant_row is not None else "local"
            )
            conn.execute(
                """
                INSERT INTO sandbox_generations (
                  id, experiment_id, project_id, tenant_id, sandbox_id, provider,
                  instance_type, gpu, price_usd_per_hour, key_id, started_at,
                  created_seq
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generation_id,
                    experiment_id,
                    project_id,
                    tenant_id,
                    sandbox_id,
                    provider,
                    instance_type,
                    gpu,
                    price_usd_per_hour,
                    # NULL for JWT/rr_sk_/local so the row shape is unchanged for
                    # every non-key provision; the mk_ key id otherwise.
                    key_id or None,
                    now,
                    next_created_seq(conn=conn, table="sandbox_generations"),
                ),
            )
        return generation_id

    def close_generation(
        self,
        *,
        experiment_id: str,
        sandbox_id: str | None = None,
        now: str | None = None,
    ) -> None:
        """Stamp ``ended_at`` on this experiment's open generation(s).

        Cost governance (cloud plan Phase 9): an open generation (``ended_at IS
        NULL``) is billed to "now" by the spend accountant; closing it on
        termination freezes its runtime so the running total stops climbing.
        Idempotent — already-closed generations are untouched. Best-effort and
        clock-injectable (the reaper passes its own ``now``).
        """
        closed_at = now or now_iso()
        with self.store.transaction() as conn:
            if sandbox_id:
                if experiment_id:
                    conn.execute(
                        "UPDATE sandbox_generations SET ended_at = ? "
                        "WHERE experiment_id = ? AND sandbox_id = ? AND ended_at IS NULL",
                        (closed_at, experiment_id, sandbox_id),
                    )
                else:
                    conn.execute(
                        "UPDATE sandbox_generations SET ended_at = ? "
                        "WHERE sandbox_id = ? AND ended_at IS NULL",
                        (closed_at, sandbox_id),
                    )
            else:
                conn.execute(
                    "UPDATE sandbox_generations SET ended_at = ? "
                    "WHERE experiment_id = ? AND ended_at IS NULL",
                    (closed_at, experiment_id),
                )

    def touch_alive(
        self, *, experiment_id: str, sandbox_uid: str, expected_project_id: str
    ) -> None:
        now = now_iso()
        with self.store.transaction() as conn:
            target_uid = str(sandbox_uid or "").strip()
            if not target_uid:
                return
            self._guarded_update(
                conn=conn,
                sandbox_uid=target_uid,
                assignments="last_seen_at = ?, updated_at = ?",
                values=[now, now],
                expected_project_id=expected_project_id,
            )

    def extend_lifetime(
        self,
        *,
        sandbox_uid: str,
        expires_at: str,
        time_limit: int,
        expected_project_id: str,
    ) -> dict[str, Any]:
        now = now_iso()
        with self.store.transaction() as conn:
            target_uid = str(sandbox_uid or "").strip()
            if not target_uid:
                raise NotFoundError("sandbox not found")
            # Status-guarded: extending a row the reaper just terminated would
            # resurrect a fresh expires_at onto a dead sandbox.
            self._guarded_update(
                conn=conn,
                sandbox_uid=target_uid,
                assignments="expires_at = ?, time_limit = ?, updated_at = ?",
                values=[expires_at, int(time_limit), now],
                expected_project_id=expected_project_id,
                extra_clause=" AND status = 'running'",
            )
            row = conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_uid = ?", (target_uid,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"sandbox not found: {target_uid}")
            if str(row["status"]) != "running":
                raise ValidationError(
                    f"sandbox {target_uid} is {row['status']}; only a running "
                    "sandbox can be extended"
                )
            return self._row_dict(row=row, conn=conn)

    def stamp_runs_observed(
        self, *, sandbox_uid: str, expected_project_id: str
    ) -> None:
        """Stamp a row's final run-receipt read (the ledger's only row write)."""
        target_uid = str(sandbox_uid or "").strip()
        if not target_uid:
            return
        with self.store.transaction() as conn:
            self._guarded_update(
                conn=conn,
                sandbox_uid=target_uid,
                assignments="runs_final_observed_at = ?",
                values=[now_iso()],
                expected_project_id=expected_project_id,
            )

    def heartbeat_snapshot(self, *, row: dict[str, Any]) -> dict[str, Any] | None:
        try:
            data = json.loads(str(row.get("heartbeat_snapshot_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def record_heartbeat(
        self,
        *,
        experiment_id: str,
        sandbox_uid: str,
        idle_since: str | None,
        snapshot: dict[str, Any],
        expected_project_id: str,
    ) -> None:
        now = now_iso()
        with self.store.transaction() as conn:
            target_uid = str(sandbox_uid or "").strip()
            if not target_uid:
                return
            self._guarded_update(
                conn=conn,
                sandbox_uid=target_uid,
                assignments=(
                    "idle_since = ?, heartbeat_snapshot_json = ?, updated_at = ?"
                ),
                values=[idle_since, json.dumps(snapshot, sort_keys=True), now],
                expected_project_id=expected_project_id,
            )

    def command_snapshot(self, *, row: dict[str, Any]) -> dict[str, Any] | None:
        command_id = str(row.get("last_command_id") or "")
        command_status = str(row.get("last_command_status") or "")
        if not command_id and not command_status:
            return None
        exit_code_raw = row.get("last_command_exit_code")
        exit_code = int(exit_code_raw) if exit_code_raw is not None else None
        return {
            "command_id": command_id or None,
            "command": str(row.get("last_command_text") or ""),
            "started_at": row.get("last_command_started_at"),
            "status": command_status or "unknown",
            "exit_code": exit_code,
            "finished_at": row.get("last_command_finished_at"),
            "output_tail": str(row.get("last_command_output_tail") or ""),
            "snapshot_at": row.get("last_command_snapshot_at"),
        }

    def record_command_snapshot(
        self, *, sandbox_uid: str, snapshot: dict[str, Any], expected_project_id: str
    ) -> dict[str, Any]:
        now = now_iso()
        command_id = str(snapshot.get("command_id") or "")
        with self.store.transaction() as conn:
            target_uid = str(sandbox_uid or "").strip()
            if not target_uid:
                return {**snapshot, "snapshot_at": now}
            row = conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_uid = ?", (target_uid,)
            ).fetchone()
            if row is not None:
                # Refuse before the read-back too: the unchanged/regressed
                # branches below hand the stored snapshot back to the caller.
                _owner_guard(
                    row=row,
                    expected_project_id=expected_project_id,
                    uid=target_uid,
                )
            existing = (
                self.command_snapshot(row=row_to_dict(row=row) or {})
                if row is not None
                else None
            )
            if existing is not None:
                # Terminal reads are the UI's poll path: skip the row write
                # when the parsed snapshot brings nothing new, and never let a
                # reader that parsed an OLDER transcript regress a newer
                # snapshot (same command finished → back to running, or an
                # earlier command replacing a later one).
                unchanged = all(
                    existing.get(key) == snapshot.get(key)
                    for key in (
                        "command_id",
                        "command",
                        "started_at",
                        "status",
                        "exit_code",
                        "finished_at",
                        "output_tail",
                    )
                )
                existing_id = str(existing.get("command_id") or "")
                same_command_regressed = (
                    existing_id == command_id
                    and bool(existing.get("finished_at"))
                    and not snapshot.get("finished_at")
                )
                older_command = (
                    existing_id != command_id
                    and bool(existing.get("started_at"))
                    and bool(snapshot.get("started_at"))
                    and str(snapshot["started_at"]) < str(existing["started_at"])
                )
                if unchanged or same_command_regressed or older_command:
                    return existing
            self._guarded_update(
                conn=conn,
                sandbox_uid=target_uid,
                assignments=(
                    "last_command_id = ?, "
                    "last_command_text = ?, "
                    "last_command_started_at = ?, "
                    "last_command_status = ?, "
                    "last_command_exit_code = ?, "
                    "last_command_finished_at = ?, "
                    "last_command_output_tail = ?, "
                    "last_command_snapshot_at = ?, "
                    "updated_at = ?"
                ),
                values=[
                    command_id,
                    str(snapshot.get("command") or ""),
                    snapshot.get("started_at"),
                    str(snapshot.get("status") or "unknown"),
                    snapshot.get("exit_code"),
                    snapshot.get("finished_at"),
                    str(snapshot.get("output_tail") or ""),
                    now,
                    now,
                ],
                expected_project_id=expected_project_id,
            )
        return {**snapshot, "snapshot_at": now}

    def mark_terminated(
        self, *, experiment_id: str, sandbox_uid: str, expected_project_id: str
    ) -> dict[str, Any]:
        return self._mark_terminal(
            experiment_id=experiment_id,
            sandbox_uid=sandbox_uid,
            status="terminated",
            expected_project_id=expected_project_id,
        )

    def mark_failed(
        self,
        *,
        experiment_id: str,
        error: str,
        sandbox_uid: str,
        expected_project_id: str,
    ) -> dict[str, Any]:
        return self._mark_terminal(
            experiment_id=experiment_id,
            sandbox_uid=sandbox_uid,
            status="failed",
            error=error,
            expected_project_id=expected_project_id,
        )

    def mark_cleanup_pending(
        self,
        *,
        sandbox_uid: str,
        detail: str,
        expected_project_id: str,
        attempts: int = 1,
        error: str | None = None,
    ) -> None:
        """Park a row whose provider deletion was never confirmed (audit SAN-05).

        Deliberately NOT terminal: attachments and the open spend generation
        both stay, because the VM may still exist and still be billing. The
        attempt count rides in `phase` and the last-attempt clock is
        `updated_at`, so the retry cadence needs no new column. `error` None
        preserves whatever verdict the row was already carrying.

        Status-qualified (CAS): two cleanup workers can hold the same pending
        row, and the one that hears "unavailable" LAST must not drag a row the
        other already terminalized back to pending — attachments and the spend
        generation are closed by then, so the resurrected row would contradict
        its own accounting. A terminal row simply does not move.
        """
        target_uid = str(sandbox_uid or "").strip()
        if not target_uid:
            return
        now = now_iso()
        assignments = ["status = ?", "phase = ?", "detail = ?", "updated_at = ?"]
        values: list[Any] = [
            CLEANUP_PENDING_STATUS,
            cleanup_attempt_phase(attempts=attempts),
            detail,
            now,
        ]
        if error is not None:
            assignments.append("error = ?")
            values.append(error)
        terminal = tuple(sorted(TERMINAL_SANDBOX_STATUSES))
        with self.store.transaction() as conn:
            self._guarded_update(
                conn=conn,
                sandbox_uid=target_uid,
                assignments=", ".join(assignments),
                values=values,
                expected_project_id=expected_project_id,
                extra_clause=(
                    " AND status NOT IN ("
                    + ", ".join(f"'{status}'" for status in terminal)
                    + ")"
                ),
            )

    def claim_cleanup_attempt(
        self,
        *,
        sandbox_uid: str,
        phase: str,
        attempts: int,
        expected_project_id: str,
        claimed_at: str,
        due_before: str | None = None,
        expected_updated_at: str | None = None,
    ) -> bool:
        """Atomically take the next cleanup attempt on a parked row (CAS).

        Re-reading a `cleanup_pending` row is a CHECK, not a claim: the daemon
        sweep, the cloud CleanupService, and a manual `sandbox.release` can all
        see the same pending row and all fire the destructive provider call —
        one VM taking several terminates, and the ledger carrying several
        confirmations for a single deletion.

        The claim advances the attempt marker in `phase` and stamps
        `updated_at` — but advancing the phase is not by itself exclusive.
        Workers arrive STAGGERED: one that re-reads the row after the winner's
        bump sees the new phase and would CAS cleanly against it, land in the
        provider call the winner is still inside, and settle the same VM twice.
        So the claim also asserts the row has not been touched since the
        claimant looked, in whichever form its caller can prove:

        - ``due_before`` (the retry sweep) — `updated_at` must be at or before
          the backoff cutoff for this attempt. The winner's stamp is its own
          `now`, so a straggler is refused until that attempt's backoff has
          elapsed: the in-flight window and the retry cadence are one window.
        - ``expected_updated_at`` (manual release) — the exact stamp the caller
          read. Release may jump the backoff queue, but never past a claim it
          never saw.

        Returns False when somebody else already holds the attempt.
        """
        target_uid = str(sandbox_uid or "").strip()
        if not target_uid:
            return False
        extra_clause = " AND status = ? AND phase = ?"
        extra_values: list[Any] = [CLEANUP_PENDING_STATUS, str(phase or "")]
        if due_before is not None:
            # An unstamped row has no window left to wait out — same reading
            # `cleanup_retry_due` gives a missing last-attempt clock.
            extra_clause += " AND (updated_at IS NULL OR updated_at <= ?)"
            extra_values.append(due_before)
        elif expected_updated_at:
            extra_clause += " AND updated_at = ?"
            extra_values.append(expected_updated_at)
        elif expected_updated_at is not None:
            extra_clause += " AND (updated_at IS NULL OR updated_at = '')"
        with self.store.transaction() as conn:
            return (
                self._guarded_update(
                    conn=conn,
                    sandbox_uid=target_uid,
                    assignments="phase = ?, updated_at = ?",
                    values=[
                        cleanup_attempt_phase(attempts=int(attempts) + 1),
                        claimed_at or now_iso(),
                    ],
                    expected_project_id=expected_project_id,
                    extra_clause=extra_clause,
                    extra_values=extra_values,
                )
                == 1
            )

    def _mark_terminal(
        self,
        *,
        experiment_id: str,
        sandbox_uid: str,
        status: str,
        expected_project_id: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Drive one sandbox row to a terminal status, closing its attachment
        and spend generation. `error` is set only on the failed path."""
        now = now_iso()
        with self.store.transaction() as conn:
            target_uid = str(sandbox_uid or "").strip()
            row = (
                conn.execute(
                    "SELECT sandbox_id, sandbox_uid, project_id FROM sandboxes "
                    "WHERE sandbox_uid = ?",
                    (target_uid,),
                ).fetchone()
                if target_uid
                else None
            )
            sandbox_id = str(row["sandbox_id"] or "") if row is not None else None
            row_uid = str(row["sandbox_uid"] or "") if row is not None else target_uid
            if row is not None:
                # Terminating is the most destructive write there is: the
                # caller's expected project rides in the predicate so a uid
                # alone can never kill another project's box (audit SAN-02).
                owner_clause, owner_values = _owner_guard(
                    row=row, expected_project_id=expected_project_id, uid=row_uid
                )
            else:
                owner_clause, owner_values = "", []
            if error is None:
                conn.execute(
                    f"""
                    UPDATE sandboxes
                    SET status = ?, terminated_at = ?, updated_at = ?
                    WHERE sandbox_uid = ?{owner_clause}
                    """,
                    (status, now, now, row_uid, *owner_values),
                )
            else:
                conn.execute(
                    f"""
                    UPDATE sandboxes
                    SET status = ?, error = ?, phase = '', detail = '',
                        terminated_at = ?, updated_at = ?
                    WHERE sandbox_uid = ?{owner_clause}
                    """,
                    (status, error, now, now, row_uid, *owner_values),
                )
            if row is not None:
                self._close_all_attachments(
                    conn=conn,
                    sandbox_uid=row_uid,
                    detached_at=now,
                )
        # Only a recorded provider id can identify this row's spend generation.
        # Close by sandbox_id ALONE: the generation may have been recorded
        # under a different experiment id than the current primary attachment
        # (anonymous request, later attach) — an experiment_id filter here
        # leaves it open and billing "to now" forever.
        if sandbox_id:
            self.close_generation(experiment_id="", sandbox_id=sandbox_id, now=now)
        elif not row_uid:
            self.close_generation(experiment_id=experiment_id, now=now)
        # sandbox_id is "" when the row never recorded one, None when the row
        # itself does not exist (the update still ran).
        return {"sandbox_id": sandbox_id, "sandbox_uid": row_uid}

    def emit_event(
        self,
        *,
        project_id: str,
        event_type: str,
        experiment_id: str,
        payload: dict[str, Any],
    ) -> None:
        with self.store.transaction() as conn:
            self.store.record_event(
                conn=conn,
                project_id=project_id,
                event_type=event_type,
                target_type="sandbox",
                target_id=experiment_id or str(payload.get("sandbox_uid") or ""),
                payload=payload,
            )

    # ---------- terminal hook plumbing ----------

    def _ensure_attachment(
        self,
        *,
        conn: Any,
        sandbox_uid: str,
        experiment_id: str,
        attached_at: str,
    ) -> None:
        if not sandbox_uid or not experiment_id:
            return
        conn.execute(
            """
            INSERT INTO sandbox_attachments (
              sandbox_uid, experiment_id, attached_at, detached_at
            )
            SELECT ?, ?, ?, NULL
            WHERE NOT EXISTS (
              SELECT 1 FROM sandbox_attachments
              WHERE sandbox_uid = ? AND experiment_id = ? AND detached_at IS NULL
            )
            """,
            (sandbox_uid, experiment_id, attached_at, sandbox_uid, experiment_id),
        )

    def _close_all_attachments(
        self, *, conn: Any, sandbox_uid: str, detached_at: str
    ) -> None:
        if not sandbox_uid:
            return
        conn.execute(
            """
            UPDATE sandbox_attachments
            SET detached_at = ?
            WHERE sandbox_uid = ? AND detached_at IS NULL
            """,
            (detached_at, sandbox_uid),
        )

    def tenant_for_sandbox(self, *, experiment_id: str, sandbox_uid: str) -> str:
        """Tenant owning a sandbox, from the row itself or its attachments.

        Sandbox rows record their tenant at upsert/attach time, so the answer
        lives entirely inside the sandbox module's own tables — the attachment
        id stays an opaque label (no research-core joins).
        """
        with closing(self.store.connect()) as conn:
            tenant = None
            if sandbox_uid:
                row = conn.execute(
                    "SELECT tenant_id FROM sandboxes WHERE sandbox_uid = ?",
                    (sandbox_uid,),
                ).fetchone()
                tenant = row["tenant_id"] if row is not None else None
            if not tenant and experiment_id:
                row = conn.execute(
                    """
                    SELECT s.tenant_id
                    FROM sandboxes s
                    JOIN sandbox_attachments a ON a.sandbox_uid = s.sandbox_uid
                    WHERE a.experiment_id = ? AND a.detached_at IS NULL
                    ORDER BY s.created_seq DESC
                    LIMIT 1
                    """,
                    (experiment_id,),
                ).fetchone()
                tenant = row["tenant_id"] if row is not None else None
        return str(tenant) if tenant else "local"


def _owner_guard(
    *, row: Any, expected_project_id: str, uid: str
) -> tuple[str, list[Any]]:
    """WHERE fragment binding a uid-keyed write to one owning project.

    The expected project is the caller's own claim about who it is writing for;
    a mismatch with the stored owner reads as "not found in that project" and
    never as a write. When a caller names nothing (legacy internal paths) the
    row's own owner still guards the statement, so a row that changed hands
    between the read and the write takes no update at all (audit SAN-02).
    """
    owner = str(row["project_id"] or "")
    expected = str(expected_project_id or "").strip()
    if expected and owner and expected != owner:
        raise NotFoundError(
            f"sandbox not found in project {expected}: {uid}",
            details={"sandbox_uid": uid, "project_id": expected},
        )
    guard = expected or owner
    return (" AND project_id = ?", [guard]) if guard else ("", [])


def _reject_ownership_change(*, row: Any, payload: dict[str, Any], uid: str) -> None:
    """Refuse an update that would move a live sandbox to another owner.

    project_id/tenant_id are set once, at insert. Letting a later write change
    them would hand another project's running VM — and its bill — to whoever
    knows the uid, leaving the real owner with an orphan (audit SAN-02).
    """
    for column in ("project_id", "tenant_id"):
        stored = str(row[column] or "")
        incoming = str(payload.get(column) or "")
        if incoming and stored and incoming != stored:
            raise ValidationError(
                f"sandbox {uid} belongs to another {column[:-3]}; sandbox "
                "ownership is immutable — call sandbox.request for a new one "
                "instead of rebinding this row",
                details={"sandbox_uid": uid, "field": column},
            )


__all__ = ["SandboxRepository"]

"""Brain-side mirror of merv_run receipts: reconcile, persist, notify.

The sandbox filesystem is the repository (merv_run writes .runs/<label>/ sentinel
files); this ledger pulls that state over the management channel the brain
already holds and keeps the `sandbox_runs` table as its durable mirror, so a
run's outcome survives the agent session AND the sandbox. run.finished is
emitted exactly once per run — the emitted flag and the event flip in one
transaction, so daemon restarts cannot double-fire.
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from .sandbox_backend import SandboxBackend
from .sandbox_support import ACTIVE_SANDBOX_STATUSES
from ..kernel.ports.mgmt_keys import MgmtKeyStore
from ..kernel.state.store import BaseStateStore, row_to_dict
from ..kernel.utils import format_iso, now_iso, parse_iso
from .repository import SandboxRepository


class SandboxRunLedger:
    """Owns every read and write of the `sandbox_runs` table."""

    def __init__(
        self,
        *,
        store: BaseStateStore,
        repository: SandboxRepository,
        backend: SandboxBackend,
        mgmt_keys: MgmtKeyStore,
    ) -> None:
        self.store = store
        self.repository = repository
        self.backend = backend
        self.mgmt_keys = mgmt_keys

    # ---------- reconcile (box filesystem -> table) ----------

    def reconcile_row(self, *, row: dict[str, Any]) -> bool:
        """Refresh records for one sandbox row from its .runs listing.

        The single remote read; every caller reaches it through `RunsObserver`,
        which owns the dedupe, the per-sandbox serialization, and the cap on
        how many boxes this process reads at once.

        A None listing ("no news": dead channel, unsupported backend) never
        mutates records — a flaky read cannot un-finish or lose a run. Only
        live sandboxes are asked; box death leaves the last mirror standing.

        Returns whether this row's receipts are now MIRRORED AND CURRENT. The
        idle reaper reads that as permission to age a receipt out of its veto,
        so False has to cover the mirror write failing too, not just the remote
        read: a receipt we saw but could not record is still work in flight.
        """
        if row.get("status") not in ACTIVE_SANDBOX_STATUSES:
            return False
        sandbox_uid = str(row.get("sandbox_uid") or "")
        sandbox_id = str(row.get("sandbox_id") or "")
        if not sandbox_uid or not sandbox_id:
            return False
        try:
            listing = self.backend.read_runs(
                sandbox_id=sandbox_id,
                workdir=str(row.get("workdir") or ""),
                ssh_host=str(row.get("ssh_host") or ""),
                ssh_port=int(row.get("ssh_port") or 0),
                ssh_user=str(row.get("ssh_user") or ""),
                key_path=str(self.mgmt_keys.key_path(sandbox_uid=sandbox_uid)),
            )
        except Exception:  # noqa: BLE001 — observation is best-effort
            return False
        if listing is None:
            return False
        if listing:
            try:
                self._record(row=row, listing=listing)
            except Exception:  # noqa: BLE001 — an unmirrored receipt is not an absent one
                return False
        return True

    def mark_final_observed(
        self,
        *,
        sandbox_uid: str,
        expected_project_id: str,
        expected_phase: str = "",
    ) -> None:
        """Record that the receipts were read successfully on the way terminal.

        Only this stamp earns the word `lost`; without it `_run_status` says
        `unknown`. Ownership-guarded like every other uid-keyed sandbox write:
        the caller names the project it read the row from (audit SAN-02), and a
        caller holding a cleanup claim names its marker so a fenced-out
        worker's stale read cannot land.
        """
        if not sandbox_uid:
            return
        self.repository.stamp_runs_observed(
            sandbox_uid=sandbox_uid,
            expected_project_id=expected_project_id,
            expected_phase=expected_phase,
        )

    def _record(
        self, *, row: dict[str, Any], listing: list[dict[str, Any]]
    ) -> None:
        sandbox_uid = str(row.get("sandbox_uid") or "")
        now = now_iso()
        with self.store.transaction() as conn:
            for run in listing:
                label = str(run.get("label") or "")
                if not label:
                    continue
                exit_code = run.get("exit_code")
                existing = conn.execute(
                    "SELECT exit_code, finished_event_emitted FROM sandbox_runs "
                    "WHERE sandbox_uid = ? AND label = ?",
                    (sandbox_uid, label),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO sandbox_runs (
                          sandbox_uid, label, command, pid, exit_code,
                          started_at, finished_at, first_seen_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sandbox_uid,
                            label,
                            str(run.get("command") or ""),
                            run.get("pid"),
                            exit_code,
                            str(run.get("started_at") or ""),
                            str(run.get("finished_at") or ""),
                            now,
                            now,
                        ),
                    )
                elif existing["exit_code"] is None:
                    # A finished record never regresses; a running one only
                    # needs its terminal facts once they appear.
                    conn.execute(
                        """
                        UPDATE sandbox_runs
                        SET command = ?, pid = ?, exit_code = ?, finished_at = ?,
                            updated_at = ?
                        WHERE sandbox_uid = ? AND label = ?
                        """,
                        (
                            str(run.get("command") or ""),
                            run.get("pid"),
                            exit_code,
                            str(run.get("finished_at") or ""),
                            now,
                            sandbox_uid,
                            label,
                        ),
                    )
                already_emitted = existing is not None and bool(
                    existing["finished_event_emitted"]
                )
                if exit_code is None or already_emitted:
                    continue
                conn.execute(
                    "UPDATE sandbox_runs SET finished_event_emitted = 1 "
                    "WHERE sandbox_uid = ? AND label = ?",
                    (sandbox_uid, label),
                )
                self.store.record_event(
                    conn=conn,
                    project_id=str(row.get("project_id") or ""),
                    event_type="run.finished",
                    target_type="sandbox",
                    target_id=str(row.get("experiment_id") or sandbox_uid),
                    payload={
                        "sandbox_uid": sandbox_uid,
                        "label": label,
                        "exit_code": int(exit_code),
                        "finished_at": str(run.get("finished_at") or ""),
                    },
                )

    # ---------- reads ----------

    def has_running_runs(
        self, *, sandbox_uid: str, fresh_since: datetime | None = None
    ) -> bool:
        """Whether a merv_run receipt says work is still in flight on this box.

        A record with no exit_code is the durable statement that a detached
        command was launched and never reported finishing — work the sampled
        CPU/GPU/network gauges cannot see (audit SAN-07): a blocked download or
        a low-CPU orchestration step reads as an idle machine.

        ``fresh_since`` drops records the ledger has not re-confirmed lately,
        so a run directory that vanished cannot veto forever. Nothing here can
        keep a box alive past its expires_at deadline — the expiry reaper does
        not consult receipts, and that paid-for lifetime stays the real ceiling.
        """
        if not sandbox_uid:
            return False
        clause = "" if fresh_since is None else " AND r.updated_at >= ?"
        params: list[Any] = [sandbox_uid]
        if fresh_since is not None:
            params.append(format_iso(fresh_since))
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                f"""
                SELECT 1 FROM sandbox_runs r
                WHERE r.sandbox_uid = ? AND r.exit_code IS NULL{clause}
                LIMIT 1
                """,
                params,
            ).fetchone()
        return row is not None

    def records_for_sandbox(self, *, sandbox_uid: str) -> list[dict[str, Any]]:
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                """
                SELECT r.*, s.status AS sandbox_status,
                       s.runs_final_observed_at AS runs_final_observed_at
                FROM sandbox_runs r
                JOIN sandboxes s ON s.sandbox_uid = r.sandbox_uid
                WHERE r.sandbox_uid = ?
                ORDER BY r.first_seen_at, r.label
                """,
                (sandbox_uid,),
            ).fetchall()
            return [row_to_dict(row=item) or {} for item in rows]

    def wait_facts(
        self, *, sandbox_uid: str, label: str
    ) -> dict[str, Any] | None:
        """Everything an auth-exempt run-wait may learn about one run.

        Deliberately narrow: a wait URL carries no credential, so this returns
        the run's terminal state and the BRAIN clocks that bound how long that
        URL stays valid — never the command, the log path, or the receipt
        clocks the box itself wrote. None means no such sandbox row.

        `present` separates a run the mirror has never seen (registration lag,
        which is a hold) from one it has seen end (which is an answer); the two
        are otherwise the same absent exit_code.
        """
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                """
                SELECT s.status AS sandbox_status,
                       s.expires_at AS expires_at,
                       s.runs_final_observed_at AS runs_final_observed_at,
                       r.label AS run_label,
                       r.exit_code AS exit_code,
                       r.updated_at AS run_updated_at
                FROM sandboxes s
                LEFT JOIN sandbox_runs r
                  ON r.sandbox_uid = s.sandbox_uid AND r.label = ?
                WHERE s.sandbox_uid = ?
                """,
                (label, sandbox_uid),
            ).fetchone()
        if row is None:
            return None
        facts = row_to_dict(row=row) or {}
        present = facts.get("run_label") is not None
        observed = str(facts.get("runs_final_observed_at") or "")
        updated = str(facts.get("run_updated_at") or "")
        return {
            "present": present,
            "status": run_status(facts) if present else "",
            "exit_code": facts.get("exit_code"),
            # ISO-8601 second precision from one writer, so the later stamp is
            # the larger string: when this process last knew anything.
            "observed_at": max(observed, updated),
            "sandbox_active": facts.get("sandbox_status") in ACTIVE_SANDBOX_STATUSES,
            "expires_at": str(facts.get("expires_at") or ""),
        }

    def records_for_experiment(self, *, experiment_id: str) -> list[dict[str, Any]]:
        """Runs across every sandbox ever attached to the experiment.

        Includes detached and terminated sandboxes on purpose: this is the
        "check back after the session (or the box) ended" read.
        """
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                """
                SELECT r.*, s.status AS sandbox_status,
                       s.runs_final_observed_at AS runs_final_observed_at
                FROM sandbox_runs r
                JOIN sandboxes s ON s.sandbox_uid = r.sandbox_uid
                WHERE r.sandbox_uid IN (
                  SELECT DISTINCT sandbox_uid FROM sandbox_attachments
                  WHERE experiment_id = ?
                )
                ORDER BY r.first_seen_at, r.label
                """,
                (experiment_id,),
            ).fetchall()
            return [row_to_dict(row=item) or {} for item in rows]

    # ---------- views ----------

    def nudge_line(self, *, sandbox_uid: str) -> str | None:
        """One compact live-runs line for sandbox.* responses, or None.

        Reads only the mirror (refreshed every daemon sweep) — attaching the
        nudge must never add a remote round-trip to an unrelated tool call.
        """
        records = self.records_for_sandbox(sandbox_uid=sandbox_uid)
        if not records:
            return None
        now = datetime.now(tz=UTC)
        live = [r for r in records if run_status(r) == "running"]
        finished = [r for r in records if run_status(r) == "finished"]
        lost = [r for r in records if run_status(r) == "lost"]
        unknown = [r for r in records if run_status(r) == "unknown"]
        parts: list[str] = []
        if live:
            shown = ", ".join(
                f"{r.get('label')} {_age(r.get('started_at'), now)}" for r in live[:3]
            )
            more = f", +{len(live) - 3} more" if len(live) > 3 else ""
            parts.append(f"{len(live)} live ({shown}{more})")
        if finished:
            shown = ", ".join(
                f"{r.get('label')}, exit {r.get('exit_code')}" for r in finished[:3]
            )
            more = f", +{len(finished) - 3} more" if len(finished) > 3 else ""
            parts.append(f"{len(finished)} finished ({shown}{more})")
        if lost:
            parts.append(f"{len(lost)} lost with the box")
        if unknown:
            parts.append(f"{len(unknown)} unknown (box died unread)")
        return "runs: " + " · ".join(parts) + " — sandbox.runs for detail"


def run_records_view(
    *,
    records: list[dict[str, Any]],
    experiment_id: str = "",
    sandbox_uid: str = "",
) -> dict[str, Any]:
    """Compact sandbox.runs response (<100 tokens typical).

    Per run: label, status, exit_code (finished only), started_at/finished_at,
    log path (experiment_dir-relative). sandbox_uid appears per run only when
    the experiment scope spans more than one sandbox.
    """
    multi_sandbox = len({str(r.get("sandbox_uid") or "") for r in records}) > 1
    runs: list[dict[str, Any]] = []
    live = finished = lost = unknown = 0
    for record in records:
        status = run_status(record)
        view: dict[str, Any] = {
            "label": record.get("label"),
            "status": status,
            "started_at": record.get("started_at") or None,
            "log": f".runs/{record.get('label')}/log.txt",
        }
        if status == "running":
            live += 1
        elif status == "finished":
            finished += 1
            view["exit_code"] = record.get("exit_code")
            view["finished_at"] = record.get("finished_at") or None
        elif status == "lost":
            lost += 1
        else:
            unknown += 1
        if multi_sandbox:
            view["sandbox_uid"] = record.get("sandbox_uid")
        runs.append(view)
    out: dict[str, Any] = {}
    if experiment_id:
        out["experiment_id"] = experiment_id
    if sandbox_uid:
        out["sandbox_uid"] = sandbox_uid
    out.update({"runs": runs, "live": live, "finished": finished})
    if lost:
        out["lost"] = lost
    if unknown:
        out["unknown"] = unknown
        out["unknown_hint"] = (
            "The box died before its receipts could be read, so these runs have "
            "no known outcome — not a failure. Treat them as unresolved."
        )
    if not runs:
        out["hint"] = (
            "No merv_run receipts. Launch anything long with "
            "`merv_run <label> -- <command>` on the sandbox: it survives SSH "
            "disconnects and reports its exit code here."
        )
    return out


def run_status(record: dict[str, Any]) -> str:
    """finished (sentinel present), running (box alive), lost, or unknown.

    `lost` and `unknown` both mean "no sentinel on a dead box", but only
    `lost` is a finding: it requires that a receipt read SUCCEEDED on the way
    to terminal and did not see one. Without that stamp the box may have died
    with its channel already broken, and the run's outcome is simply not
    known — which callers acting on the result (an agent deciding what to do
    next, a reviewer reading the record) must not be told is a failure.
    """
    if record.get("exit_code") is not None:
        return "finished"
    if record.get("sandbox_status") in ACTIVE_SANDBOX_STATUSES:
        return "running"
    return "lost" if record.get("runs_final_observed_at") else "unknown"


def _age(started_at: Any, now: datetime) -> str:
    started = parse_iso(started_at)
    if started is None:
        return "?"
    seconds = max(int((now - started).total_seconds()), 0)
    hours, minutes = seconds // 3600, (seconds % 3600) // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"

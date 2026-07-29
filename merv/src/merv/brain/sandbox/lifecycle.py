# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Provider liveness, cleanup fencing, and destructive transitions.

Provider errors mean ``unknown``, never ``gone``. Only confirmed absence may
make a row terminal; otherwise it stays visible as ``cleanup_pending``.
Terminal transitions also remove management keys and ephemeral secrets.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import threading
from typing import Any, Literal

from ..kernel.utils import format_iso, parse_iso
from ..kernel.ports.mgmt_keys import MgmtKeyStore
from .models import SandboxBackend, qualified_row_sandbox_id
from .observation import RunsObserver, SandboxRunLedger
from .models import (
    ACTIVE_SANDBOX_STATUSES,
    CLEANUP_CLAIM_REFUSED,
    CLEANUP_CLAIM_UNFENCED,
    CLEANUP_PENDING_STATUS,
    CLEANUP_RETRY_BACKOFF_SECONDS,
    CleanupClaim,
    cleanup_attempts,
    cleanup_claim_cutoff,
    cleanup_claim_expired,
    cleanup_inflight_phase,
    cleanup_inflight_token,
    cleanup_retry_due,
    new_cleanup_token,
)
from .storage import SandboxStorage


CleanupOutcome = Literal["stopped", "gone", "maybe_alive"]

CLEANUP_PENDING_REASON = (
    "the provider did not confirm the sandbox was deleted, so it may still "
    "exist and bill; the cleanup sweep keeps retrying"
)


@dataclass(frozen=True, slots=True)
class ProviderLookup:
    """Provider absence and provider outage are deliberately distinct."""

    kind: Literal["found", "not_found", "unavailable"]
    sandbox_id: str = ""
    error: str = ""


LOOKUP_NOT_FOUND = ProviderLookup(kind="not_found")


class EphemeralSecretCustody:
    """Write-only custody for a token until its post-boot delivery."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._delivered: set[str] = set()
        self._lock = threading.Lock()

    def remember(self, *, sandbox_uid: str, hf_token: str) -> None:
        if sandbox_uid and hf_token:
            with self._lock:
                self._tokens[sandbox_uid] = hf_token

    def pending(self, *, sandbox_uid: str) -> bool:
        if not sandbox_uid:
            return False
        with self._lock:
            return sandbox_uid not in self._delivered

    def hf_token(self, *, sandbox_uid: str) -> str:
        with self._lock:
            return self._tokens.get(sandbox_uid, "")

    def mark_delivered(self, *, sandbox_uid: str) -> None:
        if sandbox_uid:
            with self._lock:
                self._tokens.pop(sandbox_uid, None)
                self._delivered.add(sandbox_uid)

    def forget(self, *, sandbox_uid: str) -> None:
        if sandbox_uid:
            with self._lock:
                self._tokens.pop(sandbox_uid, None)
                self._delivered.discard(sandbox_uid)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()
            self._delivered.clear()


def _retry_cutoff(*, attempts: int, now: datetime) -> datetime:
    """Translate retry backoff into the CAS cutoff stored by the database."""
    index = min(max(attempts, 1), len(CLEANUP_RETRY_BACKOFF_SECONDS)) - 1
    return now - timedelta(seconds=CLEANUP_RETRY_BACKOFF_SECONDS[index])


class SandboxLifecycle:
    """Owns liveness policy, terminal transitions, and VM termination."""

    def __init__(
        self,
        *,
        storage: SandboxStorage,
        backend: SandboxBackend,
        mgmt_keys: MgmtKeyStore,
        secret_custody: EphemeralSecretCustody,
        observer: RunsObserver,
        runs: SandboxRunLedger,
    ) -> None:
        self.storage = storage
        self.backend = backend
        self.mgmt_keys = mgmt_keys
        self.secret_custody = secret_custody
        self.observer = observer
        self.runs = runs

    # ---------- liveness ----------

    def liveness(self, *, row: dict[str, Any] | None) -> bool | None:
        """Ask the row's recorded provider for tri-state liveness.

        ``None`` covers outages and unroutable legacy IDs; destructive callers
        must treat it as possibly alive.
        """
        if self.unreachable_owner(row=row):
            return None
        addressed, unroutable = self.addressed_id(row=row)
        if unroutable or not addressed:
            return None
        return self.liveness_of(sandbox_id=addressed)

    def liveness_of(self, *, sandbox_id: str) -> bool | None:
        """Tri-state liveness for an already provider-qualified ID."""
        if not sandbox_id:
            return None
        try:
            return bool(self.backend.is_alive(sandbox_id=sandbox_id))
        except Exception:  # noqa: BLE001
            return None

    # ---------- terminal transitions (mark + teardown, one owner) ----------

    def mark_terminated(
        self,
        *,
        row: dict[str, Any],
        expected_phase: str | None = None,
    ) -> bool:
        """Terminal mark + teardown. False when a cleanup fence refused it."""
        experiment_id = str(row.get("experiment_id") or "")
        facts = self.storage.mark_terminated(
            experiment_id=experiment_id,
            sandbox_uid=str(row.get("sandbox_uid") or ""),
            expected_project_id=str(row.get("project_id") or ""),
            expected_phase=expected_phase,
        )
        if not facts.get("landed", True):
            return False
        self._teardown(facts=facts)
        return True

    def mark_failed(
        self,
        *,
        row: dict[str, Any],
        error: str,
        expected_phase: str | None = None,
    ) -> bool:
        facts = self.storage.mark_failed(
            experiment_id=str(row.get("experiment_id") or ""),
            error=error,
            sandbox_uid=str(row.get("sandbox_uid") or ""),
            expected_project_id=str(row.get("project_id") or ""),
            expected_phase=expected_phase,
        )
        if not facts.get("landed", True):
            return False
        self._teardown(facts=facts)
        return True

    def mark_cleanup_pending(
        self,
        *,
        row: dict[str, Any],
        reason: str,
        error: str = "",
        attempts: int = 1,
        expected_phase: str | None = None,
    ) -> bool:
        """Park an unconfirmed deletion without closing spend or removing keys.

        ``expected_phase`` fences a worker that has lost its claim.
        """
        return self.storage.mark_cleanup_pending(
            sandbox_uid=str(row.get("sandbox_uid") or ""),
            detail=reason,
            expected_project_id=str(row.get("project_id") or ""),
            error=error or None,
            attempts=attempts,
            expected_phase=expected_phase,
        )

    def _park_cleanup(
        self,
        *,
        row: dict[str, Any],
        trigger: str,
        error: str = "",
        attempts: int = 0,
        expected_phase: str = "",
    ) -> dict[str, Any]:
        """Keep an unconfirmed provider deletion visible and billable."""
        attempts = max(int(attempts), 1)
        landed = self.mark_cleanup_pending(
            row=row,
            reason=CLEANUP_PENDING_REASON,
            error=error,
            attempts=attempts,
            expected_phase=expected_phase or None,
        )
        sandbox_uid = str(row.get("sandbox_uid") or "")
        current = self.storage.get_by_uid(sandbox_uid=sandbox_uid)
        if landed:
            self.storage.emit_event(
                project_id=str(row.get("project_id") or ""),
                event_type="sandbox.cleanup_pending",
                experiment_id=str(row.get("experiment_id") or ""),
                payload={
                    "sandbox_id": str(row.get("sandbox_id") or ""),
                    "sandbox_uid": sandbox_uid,
                    "trigger": trigger,
                    "attempts": attempts,
                    "reason": CLEANUP_PENDING_REASON,
                },
            )
        return current

    def _teardown(self, *, facts: dict[str, Any]) -> None:
        sandbox_uid = str(facts.get("sandbox_uid") or "")
        if sandbox_uid:
            with suppress(Exception):  # key cleanup must never block the mark
                self.mgmt_keys.remove(sandbox_uid=sandbox_uid)
            self.secret_custody.forget(sandbox_uid=sandbox_uid)

    # ---------- provider ownership ----------

    def unreachable_owner(self, *, row: dict[str, Any] | None) -> str:
        """Explain why the row's recorded provider cannot answer.

        Ownerless legacy rows use the configured backend; a missing recorded
        owner is unavailable, not evidence that the VM is gone.
        """
        recorded = str((row or {}).get("provider") or "").strip().lower()
        if not recorded:
            return ""
        try:
            caps = self.backend.capabilities_for(provider=recorded)
        except Exception as exc:  # noqa: BLE001 — unknown provider name
            return f"provider {recorded!r} is not configured ({exc})"
        answered = str(caps.name or "").strip().lower()
        if answered != recorded:
            return (
                f"provider {recorded!r} is not configured; {answered!r} would "
                "answer in its place"
            )
        return ""

    def addressed_id(self, *, row: dict[str, Any] | None) -> tuple[str, str]:
        """Return a provider-qualified ID, or why qualification failed."""
        sandbox_id = str((row or {}).get("sandbox_id") or "")
        if not sandbox_id:
            return "", ""
        try:
            return (qualified_row_sandbox_id(backend=self.backend, row=row or {}), "")
        except Exception as exc:  # noqa: BLE001 — an unroutable id is not a gone one
            return "", str(exc)

    # ---------- VM termination ----------

    def terminate_quietly(self, *, sandbox_id: str) -> None:
        with suppress(Exception):
            self.backend.terminate(sandbox_id=sandbox_id)

    def cleanup_orphan(
        self, *, experiment_id: str, row: dict[str, Any] | None
    ) -> ProviderLookup:
        """Terminate a recorded ID or deterministic-name orphan.

        The result distinguishes authoritative absence from provider outage.
        """
        unreachable_owner = self.unreachable_owner(row=row)
        if unreachable_owner:
            return ProviderLookup(kind="unavailable", error=unreachable_owner[:200])
        seen: set[str] = set()
        addressed, unroutable = self.addressed_id(row=row)
        if unroutable:
            return ProviderLookup(kind="unavailable", error=unroutable[:200])
        if addressed:
            seen.add(addressed)
            self.terminate_quietly(sandbox_id=addressed)
            return LOOKUP_NOT_FOUND
        sandbox_uid = str((row or {}).get("sandbox_uid") or "")
        active_sibling = bool(
            experiment_id
            and sandbox_uid
            and self.storage.has_active_for_experiment(
                experiment_id=experiment_id, exclude_sandbox_uid=sandbox_uid
            )
        )
        lookup_uids: list[str] = []
        if sandbox_uid:
            lookup_uids.append(sandbox_uid)
        # Broad legacy lookup could hit an active sibling with the same name.
        if not active_sibling:
            lookup_uids.append("")
        if not lookup_uids:
            lookup_uids.append("")
        unreachable = ""
        # Only ownerless rows may fan out across providers.
        provider = str((row or {}).get("provider") or "")
        for lookup_uid in lookup_uids:
            try:
                orphan = self.backend.find_sandbox_id(
                    experiment_id=experiment_id,
                    sandbox_uid=lookup_uid,
                    provider=provider,
                )
            except (
                Exception
            ) as exc:  # noqa: BLE001 — an outage is not "nothing is there"
                unreachable = str(exc)
                continue
            if orphan and str(orphan) not in seen:
                self.terminate_quietly(sandbox_id=str(orphan))
                return ProviderLookup(kind="found", sandbox_id=str(orphan))
        return (
            ProviderLookup(kind="unavailable", error=unreachable[:200])
            if unreachable
            else LOOKUP_NOT_FOUND
        )

    def observe_runs_before_terminal(
        self, *, row: dict[str, Any], acquire_timeout: float | None = None
    ) -> bool:
        """Read receipts while the VM is still reachable.

        Failure remains ``unknown``. The caller stamps success only after
        provider absence is confirmed, so a failed termination cannot lend
        false confidence to a later terminal outcome.
        """
        with suppress(Exception):  # a receipt read must never block teardown
            return bool(
                self.observer.observe_forced(
                    row=row,
                    acquire_timeout=acquire_timeout,
                )
            )
        return False

    def commit_runs_observation(
        self, *, row: dict[str, Any], observed: bool, expected_phase: str = ""
    ) -> None:
        if not observed:
            return
        with suppress(Exception):
            self.runs.mark_final_observed(
                sandbox_uid=str(row.get("sandbox_uid") or ""),
                expected_project_id=str(row.get("project_id") or ""),
                expected_phase=expected_phase,
            )

    def terminate_vm(
        self, *, row: dict[str, Any], try_direct: bool = True
    ) -> CleanupOutcome:
        """Return ``stopped``, confirmed ``gone``, or ``maybe_alive``.

        ``maybe_alive`` must remain non-terminal so a later pass retries.
        """
        experiment_id = str(row.get("experiment_id") or "")
        sandbox_id, unroutable = self.addressed_id(row=row)
        if unroutable or (row.get("sandbox_id") and self.unreachable_owner(row=row)):
            return "maybe_alive"
        stopped = False
        if sandbox_id and try_direct:
            try:
                stopped = self.backend.terminate(sandbox_id=sandbox_id)
            except Exception:  # noqa: BLE001
                stopped = False
        if stopped:
            return "stopped"
        lookup = self.cleanup_orphan(experiment_id=experiment_id, row=row)
        probe_id = sandbox_id or lookup.sandbox_id
        if probe_id:
            return (
                "gone"
                if self.liveness_of(sandbox_id=probe_id) is False
                else "maybe_alive"
            )
        # An unreachable provider is never authoritative absence.
        return "gone" if lookup.kind == "not_found" else "maybe_alive"

    def clear_for_reacquisition(
        self, *, experiment_id: str, row: dict[str, Any] | None
    ) -> CleanupOutcome:
        """Require confirmed absence before replacing a durable provider ID.

        ``maybe_alive`` parks the old row for retry. Even a row without an ID
        needs provider lookup because creation may precede ID persistence.
        """
        candidate = dict(row or {})
        candidate.setdefault("experiment_id", experiment_id)
        claim = self.claim_cleanup(row=candidate) if row is not None else None
        if row is not None and not claim:
            return "maybe_alive"
        if claim:
            candidate = self.storage.get_by_uid(
                sandbox_uid=str(candidate.get("sandbox_uid") or "")
            )
        observed = (
            self.observe_runs_before_terminal(row=candidate)
            if row is not None
            else False
        )
        outcome = self.terminate_vm(
            row=candidate,
            try_direct=bool(candidate.get("sandbox_id")),
        )
        if outcome == "maybe_alive" and row is not None:
            self._park_cleanup(
                row=candidate,
                trigger="reacquire",
                error=str(candidate.get("error") or "")
                or (
                    "a new sandbox.request arrived while this sandbox's "
                    "deletion was still unconfirmed; it never became usable"
                ),
                expected_phase=claim.phase if claim else "",
                attempts=claim.attempts if claim else 0,
            )
        elif row is not None:
            fence = claim.phase if claim else ""
            self.commit_runs_observation(
                row=candidate,
                observed=observed,
                expected_phase=fence,
            )
            if not self.mark_terminated(
                row=candidate,
                expected_phase=fence,
            ):
                return "maybe_alive"
        return outcome

    # ---------- reconcile ----------

    def reconcile(
        self,
        *,
        row: dict[str, Any],
        provisioning_job_live: bool = False,
    ) -> dict[str, Any]:
        """Converge a row without provisioning.

        Live local jobs own provisioning rows at any age; abandoned jobs settle
        only after provider cleanup is confirmed.
        """
        status = row.get("status")
        sandbox_uid = str(row.get("sandbox_uid") or "")
        if status in ACTIVE_SANDBOX_STATUSES and row.get("sandbox_id"):
            alive = self.liveness(row=row)
            if alive is None:
                return row
            if alive:
                touched = self.storage.touch_alive(
                    sandbox_uid=sandbox_uid,
                    expected_project_id=str(row.get("project_id") or ""),
                )
                if not touched:
                    return self.storage.get_by_uid(sandbox_uid=sandbox_uid)
                return self.refresh_endpoint(
                    row=self.storage.get_by_uid(sandbox_uid=sandbox_uid)
                )
            claim = self.claim_cleanup(row=row)
            if not claim:
                return self.storage.get_by_uid(sandbox_uid=sandbox_uid)
            landed = self.mark_terminated(
                row=row,
                expected_phase=claim.phase,
            )
            current = self.storage.get_by_uid(sandbox_uid=sandbox_uid)
            if landed:
                self.storage.emit_event(
                    project_id=str(row.get("project_id") or ""),
                    event_type="sandbox.expired",
                    experiment_id=str(row.get("experiment_id") or ""),
                    payload={
                        "sandbox_id": str(row.get("sandbox_id") or ""),
                        "sandbox_uid": sandbox_uid,
                    },
                )
            return current
        if status == "provisioning":
            if provisioning_job_live:
                return row
            # The job may have settled after this snapshot.
            fresh = self.storage.get_by_uid(sandbox_uid=sandbox_uid)
            if fresh.get("status") != "provisioning":
                return self.reconcile(row=fresh)
            claim = self.claim_cleanup(row=fresh)
            if not claim:
                return self.storage.get_by_uid(sandbox_uid=sandbox_uid)
            outcome = self.terminate_vm(row=fresh)
            error = "provisioning interrupted; call sandbox.request again"
            if outcome == "maybe_alive":
                return self._park_cleanup(
                    row=fresh,
                    trigger="reconcile",
                    error=error,
                    attempts=claim.attempts,
                    expected_phase=claim.phase,
                )
            landed = self.mark_failed(
                row=fresh,
                error=error,
                expected_phase=claim.phase,
            )
            current = self.storage.get_by_uid(sandbox_uid=sandbox_uid)
            if landed:
                self.storage.emit_event(
                    project_id=str(fresh.get("project_id") or ""),
                    event_type="sandbox.failed",
                    experiment_id=str(fresh.get("experiment_id") or ""),
                    payload={"error": "provisioning interrupted"},
                )
            return current
        return row

    def refresh_endpoint(self, *, row: dict[str, Any]) -> dict[str, Any]:
        """Best-effort refresh for movable endpoints such as Modal tunnels.

        Failure leaves the last known endpoint intact.
        """
        if (
            not row.get("sandbox_id")
            or row.get("status") not in ACTIVE_SANDBOX_STATUSES
        ):
            return row
        # A legacy ID must not accept another provider's endpoint.
        if self.unreachable_owner(row=row):
            return row
        sandbox_id, unroutable = self.addressed_id(row=row)
        if unroutable or not sandbox_id:
            return row
        try:
            endpoint = self.backend.refresh_ssh_endpoint(sandbox_id=sandbox_id)
        except Exception:  # noqa: BLE001 — refresh must never break the caller
            endpoint = None
        if not endpoint:
            return row
        host, port = str(endpoint[0] or ""), int(endpoint[1] or 0)
        if not host or not port:
            return row
        if host == str(row.get("ssh_host") or "") and port == int(
            row.get("ssh_port") or 0
        ):
            return row
        experiment_id = str(row.get("experiment_id") or "")
        sandbox_uid = str(row.get("sandbox_uid") or "")
        if not sandbox_uid:
            return row
        fresh = self.storage.update_endpoint(
            sandbox_uid=sandbox_uid,
            expected_project_id=str(row.get("project_id") or ""),
            ssh_host=host,
            ssh_port=port,
        )
        if fresh is None:
            return self.storage.get_by_uid(sandbox_uid=sandbox_uid)
        self.storage.emit_event(
            project_id=str(row.get("project_id")),
            event_type="sandbox.endpoint_refreshed",
            experiment_id=experiment_id,
            payload={"ssh_host": host, "ssh_port": port},
        )
        return fresh

    # ---------- reaping ----------

    def reap_expired(self, *, now: datetime | None = None) -> int:
        """Terminate running sandboxes past hard expiry."""
        now_dt = now or datetime.now(tz=UTC)
        reaped = 0
        for row in self.storage.list_running_rows():
            expires_at = parse_iso(row.get("expires_at"))
            if expires_at is None or now_dt < expires_at:
                continue
            # Provider calls age the sweep snapshot; extension may race it.
            fresh = self.storage.get_by_uid(
                sandbox_uid=str(row.get("sandbox_uid") or "")
            )
            fresh_expires = parse_iso(fresh.get("expires_at"))
            if (
                fresh.get("status") != "running"
                or fresh_expires is None
                or now_dt < fresh_expires
            ):
                continue
            if self.reap_row(row=fresh):
                reaped += 1
        return reaped

    def reap_row(
        self,
        *,
        row: dict[str, Any],
        event_type: str = "sandbox.expired",
        payload_extra: dict[str, Any] | None = None,
    ) -> bool:
        """Reap one row; park it when provider absence is unconfirmed."""
        claim = self.claim_cleanup(
            row=row,
            expected_updated_at=str(row.get("updated_at") or "") or None,
        )
        if not claim:
            return False
        observed = self.observe_runs_before_terminal(row=row)
        outcome = self.terminate_vm(row=row)
        if outcome == "maybe_alive":
            self._park_cleanup(
                row=row,
                trigger=event_type.removeprefix("sandbox."),
                attempts=claim.attempts,
                expected_phase=claim.phase,
            )
            return False
        # Provider absence makes this receipt read final before row marking.
        self.commit_runs_observation(
            row=row,
            observed=observed,
            expected_phase=claim.phase,
        )
        landed = self.mark_terminated(
            row=row,
            expected_phase=claim.phase,
        )
        if landed:
            self.storage.emit_event(
                project_id=str(row.get("project_id") or ""),
                event_type=event_type,
                experiment_id=str(row.get("experiment_id") or ""),
                payload={
                    "sandbox_id": str(row.get("sandbox_id") or ""),
                    "sandbox_uid": str(row.get("sandbox_uid") or ""),
                    "reaped": True,
                    "expires_at": row.get("expires_at"),
                    "stopped": outcome == "stopped",
                    **dict(payload_extra or {}),
                },
            )
        return landed

    # ---------- unconfirmed cleanups ----------

    def settle(
        self,
        *,
        row: dict[str, Any],
        trigger: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        error: str = "",
    ) -> CleanupOutcome:
        """Settle a pre-running path, parking unconfirmed deletion."""
        claim = self.claim_cleanup(row=row)
        if not claim:
            return "maybe_alive"
        outcome = self.terminate_vm(row=row)
        if outcome == "maybe_alive":
            self._park_cleanup(
                row=row,
                trigger=trigger,
                error=error,
                attempts=claim.attempts,
                expected_phase=claim.phase,
            )
            return outcome
        if error:
            landed = self.mark_failed(
                row=row,
                error=error,
                expected_phase=claim.phase,
            )
        else:
            landed = self.mark_terminated(
                row=row,
                expected_phase=claim.phase,
            )
        if landed:
            self.storage.emit_event(
                project_id=str(row.get("project_id") or ""),
                event_type=event_type,
                experiment_id=str(row.get("experiment_id") or ""),
                payload=dict(payload or {}),
            )
        return outcome

    def record_release_outcome(
        self,
        *,
        row: dict[str, Any],
        outcome: CleanupOutcome,
        error: str,
        claim: CleanupClaim,
    ) -> dict[str, Any]:
        """Persist a provider-confirmed release or park the fenced cleanup."""
        if outcome == "maybe_alive":
            return self._park_cleanup(
                row=row,
                trigger="release",
                error=error,
                attempts=claim.attempts,
                expected_phase=claim.phase,
            )
        experiment_id = str(row.get("experiment_id") or "")
        sandbox_uid = str(row.get("sandbox_uid") or "")
        project_id = str(row.get("project_id") or "")
        if error:
            landed = self.mark_failed(
                row=row,
                error=error,
                expected_phase=claim.phase,
            )
        else:
            landed = self.mark_terminated(
                row=row,
                expected_phase=claim.phase,
            )
        current = self.storage.get_by_uid(sandbox_uid=sandbox_uid)
        if landed:
            self.storage.emit_event(
                project_id=project_id,
                event_type="sandbox.released",
                experiment_id=experiment_id,
                payload={
                    "sandbox_id": str(row.get("sandbox_id") or ""),
                    "sandbox_uid": sandbox_uid,
                    "active_experiment_ids": [
                        str(value)
                        for value in row.get("active_experiment_ids") or []
                        if str(value)
                    ],
                    "stopped": outcome == "stopped",
                    "status": "failed" if error else "terminated",
                },
            )
        return current

    def retry_cleanup_pending(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Retry forever with backoff while a VM may still be billing."""
        now_dt = now or datetime.now(tz=UTC)
        rows = self.storage.list_rows_by_status(status=CLEANUP_PENDING_STATUS)
        confirmed = retried = 0
        for row in rows:
            attempts = cleanup_attempts(phase=row.get("phase"))
            last_attempt_at = parse_iso(row.get("updated_at"))
            if cleanup_inflight_token(phase=row.get("phase")):
                # Reclaim only after the in-flight deadline.
                if not cleanup_claim_expired(claimed_at=last_attempt_at, now=now_dt):
                    continue
            elif not cleanup_retry_due(
                attempts=attempts,
                last_attempt_at=last_attempt_at,
                now=now_dt,
            ):
                continue
            try:
                if self._retry_one_cleanup(row=row, attempts=attempts, now=now_dt):
                    confirmed += 1
                else:
                    retried += 1
            except Exception:  # noqa: BLE001 — one bad row never aborts the sweep
                retried += 1
        pending = len(rows) - confirmed
        return {
            "ok": pending == 0,
            "pending": pending,
            "confirmed": confirmed,
            "retried": retried,
        }

    def claim_cleanup(
        self,
        *,
        row: dict[str, Any],
        expected_updated_at: str | None = None,
    ) -> CleanupClaim:
        """Claim cleanup by CAS; manual release may jump backoff, not ownership."""
        return self._claim_cleanup(
            row=row,
            expected_updated_at=expected_updated_at,
        )

    def claim_cleanup_due(self, *, row: dict[str, Any], now: datetime) -> CleanupClaim:
        """Claim a due retry without crossing another worker's in-flight marker."""
        return self._claim_cleanup(
            row=row,
            now=now,
            due_before=format_iso(
                _retry_cutoff(
                    attempts=cleanup_attempts(phase=row.get("phase")), now=now
                )
            ),
        )

    def _claim_cleanup(
        self,
        *,
        row: dict[str, Any],
        now: datetime | None = None,
        expected_updated_at: str | None = None,
        due_before: str | None = None,
    ) -> CleanupClaim:
        """Shared claim: a conditional write is the only exclusive read."""
        status = str(row.get("status") or "")
        if status not in ACTIVE_SANDBOX_STATUSES | {
            "provisioning",
            CLEANUP_PENDING_STATUS,
        }:
            return CLEANUP_CLAIM_REFUSED
        sandbox_uid = str(row.get("sandbox_uid") or "")
        if not sandbox_uid:
            return CLEANUP_CLAIM_UNFENCED
        now_dt = now or datetime.now(tz=UTC)
        phase = str(row.get("phase") or "")
        stale_before: str | None = None
        if cleanup_inflight_token(phase=phase):
            if not cleanup_claim_expired(
                claimed_at=parse_iso(row.get("updated_at")), now=now_dt
            ):
                return CLEANUP_CLAIM_REFUSED
            # Reclaim a dead holder; the new token fences out its late write.
            stale_before = format_iso(cleanup_claim_cutoff(now=now_dt))
            due_before = None
        attempts = cleanup_attempts(phase=phase)
        token = new_cleanup_token()
        claimed = self.storage.claim_cleanup_attempt(
            sandbox_uid=sandbox_uid,
            phase=phase,
            attempts=attempts,
            expected_project_id=str(row.get("project_id") or ""),
            # One clock anchors both backoff and the in-flight deadline.
            claimed_at=format_iso(now_dt),
            token=token,
            expected_status=status,
            expected_updated_at=expected_updated_at,
            due_before=due_before,
            stale_before=stale_before,
        )
        if not claimed:
            return CLEANUP_CLAIM_REFUSED
        return CleanupClaim(
            granted=True,
            token=token,
            attempts=attempts + 1,
            phase=cleanup_inflight_phase(attempts=attempts + 1, token=token),
        )

    def _retry_one_cleanup(
        self, *, row: dict[str, Any], attempts: int, now: datetime | None = None
    ) -> bool:
        now_dt = now or datetime.now(tz=UTC)
        experiment_id = str(row.get("experiment_id") or "")
        sandbox_uid = str(row.get("sandbox_uid") or "")
        project_id = str(row.get("project_id") or "")
        # Multiple sweepers share these rows; reread before remote I/O.
        if sandbox_uid:
            fresh = self.storage.get_by_uid(sandbox_uid=sandbox_uid)
            if fresh.get("status") != CLEANUP_PENDING_STATUS:
                return True
            row = fresh
            attempts = cleanup_attempts(phase=row.get("phase")) or attempts
        claim = self.claim_cleanup_due(row=row, now=now_dt)
        if not claim:
            return False
        attempts = claim.attempts or attempts + 1
        fence = claim.phase or None
        # Preserve the verdict the row was headed toward before cleanup stalled.
        origin_error = str(row.get("error") or "")
        observed = self.observe_runs_before_terminal(row=row)
        outcome = self.terminate_vm(row=row)
        if outcome == "maybe_alive":
            if not self.mark_cleanup_pending(
                row=row,
                reason=str(row.get("detail") or CLEANUP_PENDING_REASON),
                error=origin_error,
                attempts=attempts,
                expected_phase=fence,
            ):
                return False  # fenced out: this attempt no longer owns the row
            self.storage.emit_event(
                project_id=project_id,
                event_type="sandbox.cleanup_retried",
                experiment_id=experiment_id,
                payload={
                    "sandbox_id": str(row.get("sandbox_id") or ""),
                    "sandbox_uid": sandbox_uid,
                    "attempts": attempts,
                    "confirmed": False,
                },
            )
            return False
        self.commit_runs_observation(
            row=row,
            observed=observed,
            expected_phase=fence or "",
        )
        if origin_error:
            settled = self.mark_failed(
                row=row,
                error=origin_error,
                expected_phase=fence,
            )
        else:
            settled = self.mark_terminated(
                row=row,
                expected_phase=fence,
            )
        if not settled:
            return False  # fenced out: the reclaiming worker owns the ending
        self.storage.emit_event(
            project_id=project_id,
            event_type="sandbox.cleanup_confirmed",
            experiment_id=experiment_id,
            payload={
                "sandbox_id": str(row.get("sandbox_id") or ""),
                "sandbox_uid": sandbox_uid,
                "attempts": attempts,
                "stopped": outcome == "stopped",
                "status": "failed" if origin_error else "terminated",
            },
        )
        return True

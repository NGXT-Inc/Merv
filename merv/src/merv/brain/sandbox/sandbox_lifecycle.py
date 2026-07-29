# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Provider liveness, cleanup fencing, and destructive transitions.

Provider errors mean ``unknown``, never ``gone``. Only confirmed absence may
make a row terminal; otherwise it stays visible as ``cleanup_pending``.
Terminal transitions also remove management keys and ephemeral secrets.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from ..kernel.utils import format_iso
from ..kernel.ports.mgmt_keys import MgmtKeyStore
from .sandbox_backend import SandboxBackend, qualified_row_sandbox_id
from .core import (
    CLEANUP_PENDING_REASON,
    LOOKUP_NOT_FOUND,
    CleanupOutcome,
    LifecycleDecision,
    ProviderLookup,
    cleanup_pending_decision,
    lookup_found,
    lookup_unavailable,
    reap_decision,
    reconcile_decision,
    settle_decision,
)
from .keys import EphemeralSecretCustody
from .sandbox_support import (
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
    parse_iso,
)
from .storage import SandboxStorage


# Fast path for an in-process acquisition; durable state remains authoritative.
JobProbe = Callable[..., bool]


def _retry_cutoff(*, attempts: int, now: datetime) -> datetime:
    """Translate retry backoff into the CAS cutoff stored by the database."""
    index = min(max(attempts, 1), len(CLEANUP_RETRY_BACKOFF_SECONDS)) - 1
    return now - timedelta(seconds=CLEANUP_RETRY_BACKOFF_SECONDS[index])


class SandboxLifecycle:
    """Owns liveness policy, terminal transitions, and VM termination."""

    def __init__(
        self,
        *,
        repository: SandboxStorage,
        backend: SandboxBackend,
        mgmt_keys: MgmtKeyStore,
        secret_custody: EphemeralSecretCustody,
    ) -> None:
        self.repository = repository
        self.backend = backend
        self.mgmt_keys = mgmt_keys
        self.secret_custody = secret_custody
        self.job_probe: JobProbe | None = None
        self.observe_runs: Callable[..., bool] | None = None
        self.stamp_runs_observed: Callable[..., None] | None = None

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

    def _job_is_live(self, *, experiment_id: str, sandbox_uid: str) -> bool:
        if self.job_probe is None:
            return False
        try:
            return bool(
                self.job_probe(experiment_id=experiment_id, sandbox_uid=sandbox_uid)
            )
        except Exception:  # noqa: BLE001 — a probe failure must not kill a row
            return False

    # ---------- terminal transitions (mark + teardown, one owner) ----------

    def mark_terminated(
        self,
        *,
        experiment_id: str,
        sandbox_uid: str,
        expected_project_id: str,
        expected_phase: str | None = None,
    ) -> bool:
        """Terminal mark + teardown. False when a cleanup fence refused it."""
        facts = self.repository.mark_terminated(
            experiment_id=experiment_id,
            sandbox_uid=sandbox_uid,
            expected_project_id=expected_project_id,
            expected_phase=expected_phase,
        )
        if not facts.get("landed", True):
            return False
        self._teardown(experiment_id=experiment_id, facts=facts)
        return True

    def mark_failed(
        self,
        *,
        experiment_id: str,
        error: str,
        sandbox_uid: str,
        expected_project_id: str,
        expected_phase: str | None = None,
    ) -> bool:
        facts = self.repository.mark_failed(
            experiment_id=experiment_id,
            error=error,
            sandbox_uid=sandbox_uid,
            expected_project_id=expected_project_id,
            expected_phase=expected_phase,
        )
        if not facts.get("landed", True):
            return False
        self._teardown(experiment_id=experiment_id, facts=facts)
        return True

    def mark_cleanup_pending(
        self,
        *,
        sandbox_uid: str,
        reason: str,
        expected_project_id: str,
        error: str = "",
        attempts: int = 1,
        expected_phase: str | None = None,
    ) -> bool:
        """Park an unconfirmed deletion without closing spend or removing keys.

        ``expected_phase`` fences a worker that has lost its claim.
        """
        return self.repository.mark_cleanup_pending(
            sandbox_uid=sandbox_uid,
            detail=reason,
            expected_project_id=expected_project_id,
            error=error or None,
            attempts=attempts,
            expected_phase=expected_phase,
        )

    def _teardown(self, *, experiment_id: str, facts: dict[str, Any]) -> None:
        _ = experiment_id
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
            return lookup_unavailable(unreachable_owner)
        seen: set[str] = set()
        addressed, unroutable = self.addressed_id(row=row)
        if unroutable:
            return lookup_unavailable(unroutable)
        if addressed:
            seen.add(addressed)
            self.terminate_quietly(sandbox_id=addressed)
            return LOOKUP_NOT_FOUND
        sandbox_uid = str((row or {}).get("sandbox_uid") or "")
        active_sibling = bool(
            experiment_id
            and sandbox_uid
            and self.repository.has_active_for_experiment(
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
                return lookup_found(str(orphan))
        return lookup_unavailable(unreachable) if unreachable else LOOKUP_NOT_FOUND

    def observe_runs_before_terminal(
        self, *, row: dict[str, Any], acquire_timeout: float | None = None
    ) -> bool:
        """Read receipts while the VM is still reachable.

        Failure remains ``unknown``. The caller stamps success only after
        provider absence is confirmed, so a failed termination cannot lend
        false confidence to a later terminal outcome.
        """
        if self.observe_runs is None:
            return False
        with suppress(Exception):  # a receipt read must never block teardown
            return bool(self.observe_runs(row=row, acquire_timeout=acquire_timeout))
        return False

    def commit_runs_observation(
        self, *, row: dict[str, Any], observed: bool, expected_phase: str = ""
    ) -> None:
        if not observed or self.stamp_runs_observed is None:
            return
        with suppress(Exception):
            self.stamp_runs_observed(
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
        outcome = self.terminate_vm(
            row=candidate,
            try_direct=bool(candidate.get("sandbox_id")),
        )
        if outcome == "maybe_alive" and row is not None:
            self.apply(
                row=row,
                decision=cleanup_pending_decision(
                    row=row,
                    trigger="reacquire",
                    error=str(row.get("error") or "")
                    or (
                        "a new sandbox.request arrived while this sandbox's "
                        "deletion was still unconfirmed; it never became usable"
                    ),
                ),
            )
        return outcome

    def apply(
        self, *, row: dict[str, Any], decision: LifecycleDecision
    ) -> dict[str, Any]:
        experiment_id = str(row.get("experiment_id") or "")
        sandbox_uid = str(row.get("sandbox_uid") or "")
        project_id = str(row.get("project_id") or "")
        current = row
        # A fenced-out transition must not emit an event for a write that lost.
        fenced_out = False
        for intent in decision.intents:
            fence = str(intent.payload.get("expected_phase") or "") or None
            if intent.kind == "mark_cleanup_pending":
                landed = self.mark_cleanup_pending(
                    sandbox_uid=sandbox_uid,
                    reason=str(intent.payload.get("reason") or ""),
                    expected_project_id=project_id,
                    error=str(intent.payload.get("error") or ""),
                    attempts=int(intent.payload.get("attempts") or 1),
                    expected_phase=fence,
                )
                fenced_out |= fence is not None and not landed
                current = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
            elif intent.kind == "mark_failed":
                landed = self.mark_failed(
                    experiment_id=experiment_id,
                    sandbox_uid=sandbox_uid,
                    error=str(intent.payload.get("error") or "sandbox failed"),
                    expected_project_id=project_id,
                    expected_phase=fence,
                )
                fenced_out |= fence is not None and not landed
                current = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
            elif intent.kind == "mark_terminated":
                landed = self.mark_terminated(
                    experiment_id=experiment_id,
                    sandbox_uid=sandbox_uid,
                    expected_project_id=project_id,
                    expected_phase=fence,
                )
                fenced_out |= fence is not None and not landed
                current = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
            elif intent.kind == "touch_alive":
                self.repository.touch_alive(
                    experiment_id=experiment_id,
                    sandbox_uid=sandbox_uid,
                    expected_project_id=project_id,
                )
                current = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
            elif intent.kind == "refresh_endpoint":
                current = self.refresh_endpoint(row=current)
        if decision.event is not None and not fenced_out:
            self.repository.emit_event(
                project_id=str(row.get("project_id") or ""),
                event_type=decision.event.type,
                experiment_id=experiment_id,
                payload=dict(decision.event.payload),
            )
        return current

    # ---------- reconcile ----------

    def reconcile(self, *, row: dict[str, Any]) -> dict[str, Any]:
        """Converge a row without provisioning.

        Live local jobs own provisioning rows at any age; abandoned jobs settle
        only after provider cleanup is confirmed.
        """
        status = row.get("status")
        sandbox_uid = str(row.get("sandbox_uid") or "")
        if status in ACTIVE_SANDBOX_STATUSES and row.get("sandbox_id"):
            alive = self.liveness(row=row)
            if alive is not False:
                return self.apply(
                    row=row,
                    decision=reconcile_decision(row=row, alive=alive),
                )
            claim = self.claim_cleanup(row=row)
            if not claim:
                return self.repository.get_by_uid(sandbox_uid=sandbox_uid)
            return self.apply(
                row=row,
                decision=reconcile_decision(
                    row=row,
                    alive=False,
                    fence_phase=claim.phase,
                    attempts=claim.attempts,
                ),
            )
        if status == "provisioning":
            experiment_id = str(row.get("experiment_id") or "")
            if self._job_is_live(experiment_id=experiment_id, sandbox_uid=sandbox_uid):
                return row
            # The job may have settled after this snapshot.
            fresh = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
            if fresh.get("status") != "provisioning":
                return self.reconcile(row=fresh)
            claim = self.claim_cleanup(row=fresh)
            if not claim:
                return self.repository.get_by_uid(sandbox_uid=sandbox_uid)
            return self.apply(
                row=fresh,
                decision=reconcile_decision(
                    row=fresh,
                    alive=None,
                    job_live=False,
                    cleanup=self.terminate_vm(row=fresh),
                    fence_phase=claim.phase,
                    attempts=claim.attempts,
                ),
            )
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
        self.repository.upsert(
            experiment_id=experiment_id,
            sandbox_uid=sandbox_uid,
            expected_project_id=str(row.get("project_id") or ""),
            ssh_host=host,
            ssh_port=port,
        )
        fresh = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
        self.repository.emit_event(
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
        for row in self.repository.list_running_rows():
            expires_at = parse_iso(row.get("expires_at"))
            if expires_at is None or now_dt < expires_at:
                continue
            # Provider calls age the sweep snapshot; extension may race it.
            fresh = self.repository.get_by_uid(
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
        claim = self.claim_cleanup(row=row)
        if not claim:
            return False
        observed = self.observe_runs_before_terminal(row=row)
        outcome = self.terminate_vm(row=row)
        if outcome != "maybe_alive":
            # Provider absence makes this receipt read final before row marking.
            self.commit_runs_observation(
                row=row,
                observed=observed,
                expected_phase=claim.phase,
            )
        applied = self.apply(
            row=row,
            decision=reap_decision(
                row=row,
                outcome=outcome,
                event_type=event_type,
                payload_extra=payload_extra,
                fence_phase=claim.phase,
                attempts=claim.attempts,
            ),
        )
        return outcome != "maybe_alive" and str(applied.get("status") or "") in {
            "terminated",
            "failed",
        }

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
        self.apply(
            row=row,
            decision=settle_decision(
                row=row,
                outcome=outcome,
                trigger=trigger,
                event_type=event_type,
                payload=payload,
                error=error,
                fence_phase=claim.phase,
                attempts=claim.attempts,
            ),
        )
        return outcome

    def retry_cleanup_pending(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Retry forever with backoff while a VM may still be billing."""
        now_dt = now or datetime.now(tz=UTC)
        rows = self.repository.list_rows_by_status(status=CLEANUP_PENDING_STATUS)
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

    def claim_cleanup(self, *, row: dict[str, Any]) -> CleanupClaim:
        """Claim cleanup by CAS; manual release may jump backoff, not ownership."""
        return self._claim_cleanup(row=row)

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
        claimed = self.repository.claim_cleanup_attempt(
            sandbox_uid=sandbox_uid,
            phase=phase,
            attempts=attempts,
            expected_project_id=str(row.get("project_id") or ""),
            # One clock anchors both backoff and the in-flight deadline.
            claimed_at=format_iso(now_dt),
            token=token,
            expected_status=status,
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
            fresh = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
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
        outcome = self.terminate_vm(row=row)
        if outcome == "maybe_alive":
            if not self.mark_cleanup_pending(
                sandbox_uid=sandbox_uid,
                reason=str(row.get("detail") or CLEANUP_PENDING_REASON),
                expected_project_id=project_id,
                error=origin_error,
                attempts=attempts,
                expected_phase=fence,
            ):
                return False  # fenced out: this attempt no longer owns the row
            self.repository.emit_event(
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
        if origin_error:
            settled = self.mark_failed(
                experiment_id=experiment_id,
                sandbox_uid=sandbox_uid,
                error=origin_error,
                expected_project_id=project_id,
                expected_phase=fence,
            )
        else:
            settled = self.mark_terminated(
                experiment_id=experiment_id,
                sandbox_uid=sandbox_uid,
                expected_project_id=project_id,
                expected_phase=fence,
            )
        if not settled:
            return False  # fenced out: the reclaiming worker owns the ending
        self.repository.emit_event(
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

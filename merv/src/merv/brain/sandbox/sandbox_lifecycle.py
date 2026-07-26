"""Single owner of sandbox status transitions and destructive decisions.

Every path that terminates a provider VM or drives a row to a terminal
status routes through `SandboxLifecycle` — the reaper, release, reconcile,
and the provisioner's settle paths. Concentrating that authority here keeps
the invariants in one place:

  - provider-API errors are never read as "instance gone" (tri-state
    `liveness`, typed `ProviderLookup`); a row goes terminal only once the
    provider confirms the VM is not alive — a terminated row over a live VM
    bills invisibly forever, and no sweep revisits terminated rows. Everything
    else parks as `cleanup_pending`, which stays visible and gets retried;
  - a terminal mark always removes its management key, regardless of which
    caller marked it;
  - a live provisioning job owns its row at any age — only the lifecycle's
    job probe decides whether "provisioning" means in-flight or wedged.

The repository stays persistence-only; the provisioner keeps job threads; the
daemons keep scheduling. None of them decide life or death.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from ..kernel.utils import format_iso
from ..kernel.ports.mgmt_keys import MgmtKeyStore
from .sandbox_backend import SandboxBackend
from .lifecycle_reducer import (
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
from .repository import SandboxRepository


# Probe for an in-process provisioning job thread; wired to
# SandboxProvisioner.job_is_live by the facade after both exist.
JobProbe = Callable[..., bool]


def _retry_cutoff(*, attempts: int, now: datetime) -> datetime:
    """The newest `updated_at` still due for a retry — `cleanup_retry_due`'s
    window expressed as a bound the claim's WHERE clause can carry."""
    index = min(max(attempts, 1), len(CLEANUP_RETRY_BACKOFF_SECONDS)) - 1
    return now - timedelta(seconds=CLEANUP_RETRY_BACKOFF_SECONDS[index])


class SandboxLifecycle:
    """Owns liveness policy, terminal transitions, and VM termination."""

    def __init__(
        self,
        *,
        repository: SandboxRepository,
        backend: SandboxBackend,
        mgmt_keys: MgmtKeyStore,
    ) -> None:
        self.repository = repository
        self.backend = backend
        self.mgmt_keys = mgmt_keys
        self.job_probe: JobProbe | None = None
        # Wired post-construction (like job_probe) to keep the ledger and the
        # lifecycle peers rather than making one import the other.
        self.observe_runs: Callable[..., bool] | None = None
        self.stamp_runs_observed: Callable[..., None] | None = None

    # ---------- liveness ----------

    def liveness(self, *, row: dict[str, Any] | None) -> bool | None:
        """Tri-state liveness for a ROW, asked of the provider the ROW records.

        True/False when that provider answered authoritatively, None when it
        could not be asked — an outage, a timeout, an id nobody can route, or a
        recorded owner that is no longer in ``MERV_EXECUTION_BACKENDS``.

        Routing is never left to the id alone: a legacy un-prefixed id carries
        no owner, so asking whichever backend happens to be the default today
        turns a wrong-provider 404 into "dead" and strands a live, billing VM
        behind a terminated row (audit SAN-06). An unreachable owner is
        `unavailable`, never a false "dead".

        Callers making destructive decisions (terminate, mark_terminated,
        re-provision) must treat None as "possibly alive" — collapsing it to
        False is how a healthy VM ends up killed or stranded behind a
        terminated row, billing invisibly.
        """
        if self.unreachable_owner(row=row):
            return None
        addressed, unroutable = self.addressed_id(row=row)
        if unroutable or not addressed:
            return None
        return self.liveness_of(sandbox_id=addressed)

    def liveness_of(self, *, sandbox_id: str) -> bool | None:
        """Tri-state liveness for an id that ALREADY names its owner.

        Only for ids that came back from ``addressed_id`` or the multiplexer's
        own lookup; everything row-shaped goes through ``liveness``.
        """
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
        """Park a row whose provider deletion was never confirmed.

        No teardown: the management key and the open spend generation both stay,
        because the VM may still be up — and if it is, we still need to reach it
        and it is still billing.

        ``expected_phase`` is the in-flight marker this worker claimed; passing
        it makes the re-park a no-op once another worker has reclaimed the row.
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
        """Drop a terminal sandbox's control-side management key."""
        _ = experiment_id
        sandbox_uid = str(facts.get("sandbox_uid") or "")
        if sandbox_uid:
            with suppress(Exception):  # key cleanup must never block the mark
                self.mgmt_keys.remove(sandbox_uid=sandbox_uid)

    # ---------- provider ownership ----------

    def unreachable_owner(self, *, row: dict[str, Any] | None) -> str:
        """Why the provider that owns this row cannot be asked, or "".

        A row records the provider that served it. When that provider is no
        longer in ``MERV_EXECUTION_BACKENDS`` nobody can answer for it, and the
        remaining providers all truthfully saying "not mine" must NOT be read
        as "the VM is gone" (audit SAN-06) — that is a live, billing VM behind
        a terminal row. An empty ``provider`` is a pre-multiplexer row: the
        configured backend is all there ever was, so it stays reachable.
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
        """``(id to address the provider with, why it cannot be addressed)``.

        Legacy ids carry no provider prefix, so only the row knows who owns
        them; the backend turns the pair into a routable id, or refuses.
        """
        sandbox_id = str((row or {}).get("sandbox_id") or "")
        if not sandbox_id:
            return "", ""
        try:
            return (
                str(
                    self.backend.qualified_sandbox_id(
                        sandbox_id=sandbox_id,
                        provider=str((row or {}).get("provider") or ""),
                    )
                ),
                "",
            )
        except Exception as exc:  # noqa: BLE001 — an unroutable id is not a gone one
            return "", str(exc)

    # ---------- VM termination ----------

    def terminate_quietly(self, *, sandbox_id: str) -> None:
        with suppress(Exception):
            self.backend.terminate(sandbox_id=sandbox_id)

    def cleanup_orphan(
        self, *, experiment_id: str, row: dict[str, Any] | None
    ) -> ProviderLookup:
        """Best-effort terminate any sandbox tied to this experiment.

        Covers both a recorded sandbox_id (from a prior/failed row) and the
        deterministic-named orphan a dead job may have left on the backend.

        Returns what the deterministic-name probe learned, so the caller can
        tell "the provider answered and named nothing" from "the provider could
        not be asked" (audit SAN-06). A recorded id makes the probe unnecessary
        — there ``liveness`` is the authority — and reads as ``not_found``.
        """
        # Route by the row's durable owner first: a provider that was dropped
        # from the configuration cannot be asked, and the ones that remain
        # answering "not mine" is not evidence (audit SAN-06).
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
        # Legacy fallback: old providers may only be findable by the
        # experiment-derived deterministic name. Skip that broad lookup while
        # another sandbox that may still exist — parked ones included — is
        # attached to the same experiment and answers to that same name.
        if not active_sibling:
            lookup_uids.append("")
        if not lookup_uids:
            lookup_uids.append("")
        unreachable = ""
        # Row-owner routing, the same rule `liveness` follows: the deterministic
        # name is derived from the EXPERIMENT, so a sibling attempt on another
        # provider answers to it too. Searching the fleet and taking the first
        # hit terminates that sibling's VM and then reads its answer as proof
        # this row's sandbox is gone (audit SAN-06). Only a row that records no
        # owner may fan out.
        provider = str((row or {}).get("provider") or "")
        for lookup_uid in lookup_uids:
            try:
                orphan = self.backend.find_sandbox_id(
                    experiment_id=experiment_id,
                    sandbox_uid=lookup_uid,
                    provider=provider,
                )
            except Exception as exc:  # noqa: BLE001 — an outage is not "nothing is there"
                unreachable = str(exc)
                continue
            if orphan and str(orphan) not in seen:
                self.terminate_quietly(sandbox_id=str(orphan))
                return lookup_found(str(orphan))
        return lookup_unavailable(unreachable) if unreachable else LOOKUP_NOT_FOUND

    def observe_runs_before_terminal(self, *, row: dict[str, Any]) -> bool:
        """Read receipts one last time while the row is still active.

        Every terminal path calls this FIRST: once the row leaves
        ACTIVE_SANDBOX_STATUSES the ledger refuses to read it and the VM is
        gone anyway, so a run that finished seconds earlier would be recorded
        as never having finished. Best-effort by construction — a failure
        simply returns False, which leaves the observation unstamped and reads
        downstream as `unknown` rather than `lost`.

        Returns whether the read succeeded. The caller stamps it (via
        `commit_runs_observation`) only once the provider confirms the VM is
        gone: a terminate that comes back `maybe_alive` leaves the row running,
        and this observation must not outlive the attempt. Stamping a row that
        is still active is harmless — `run_status` consults the status first.

        Note this covers the reap and release paths. The liveness-reconcile and
        provisioner mark paths do not observe, by design: there the provider has
        already reported the VM gone, so there is nothing left to read and
        `unknown` is the honest answer.
        """
        if self.observe_runs is None:
            return False
        with suppress(Exception):  # a receipt read must never block teardown
            return bool(self.observe_runs(row=row))
        return False

    def commit_runs_observation(self, *, row: dict[str, Any], observed: bool) -> None:
        """Stamp a successful pre-terminal read, once the row is really gone."""
        if not observed or self.stamp_runs_observed is None:
            return
        with suppress(Exception):
            self.stamp_runs_observed(
                sandbox_uid=str(row.get("sandbox_uid") or ""),
                expected_project_id=str(row.get("project_id") or ""),
            )

    def terminate_vm(
        self, *, row: dict[str, Any], try_direct: bool = True
    ) -> CleanupOutcome:
        """Terminate the provider VM behind a row. Returns:

        - ``"stopped"`` — the provider confirmed the terminate;
        - ``"gone"`` — terminate failed/skipped but the provider answered
          authoritatively that the VM is not alive;
        - ``"maybe_alive"`` — terminate failed and the VM may still be up, or
          the provider could not be asked: the caller must NOT mark the row
          terminal, so a later pass retries instead of stranding a billing VM.
        """
        experiment_id = str(row.get("experiment_id") or "")
        # Ask the provider the ROW names, never whichever one is configured
        # today: an id we cannot route is an unasked provider, which is exactly
        # the case that must stay `maybe_alive`.
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
        # Direct terminate failed or there was no recorded id: try the
        # deterministic-name orphan cleanup path, then require confirmation.
        lookup = self.cleanup_orphan(experiment_id=experiment_id, row=row)
        probe_id = sandbox_id or lookup.sandbox_id
        if probe_id:
            # probe_id already names its owner (addressed_id, or the
            # multiplexer's own prefixed lookup hit).
            return (
                "gone" if self.liveness_of(sandbox_id=probe_id) is False else "maybe_alive"
            )
        # Nothing to probe: only an authoritative "the provider named no such
        # sandbox" clears this row. An unreachable provider does not.
        return "gone" if lookup.kind == "not_found" else "maybe_alive"

    def clear_for_reacquisition(
        self, *, experiment_id: str, row: dict[str, Any] | None
    ) -> CleanupOutcome:
        """Confirm a prior sandbox is gone BEFORE its row is rewritten.

        A fresh ``sandbox.request`` after a brain restart finds no live job and
        re-provisions over the old row. That row's ``sandbox_id`` is the only
        record of a VM that may still be up and billing, so a best-effort
        terminate is not enough here: rewriting it on an unconfirmed cleanup
        erases the id and the money leaks invisibly (audit SAN-05).

        So the outcome is authoritative. ``maybe_alive`` parks the row — it
        keeps its provider id, stays visible, and the retry sweep keeps asking
        — and the caller provisions onto a FRESH row instead. A row with no
        recorded id has nothing to lose, so it keeps the cheap best-effort
        deterministic-name sweep and always reads as cleared.
        """
        if not str((row or {}).get("sandbox_id") or ""):
            self.cleanup_orphan(experiment_id=experiment_id, row=row)
            return "gone"
        assert row is not None  # narrowed by the recorded-id check above
        outcome = self.terminate_vm(row=row)
        if outcome == "maybe_alive":
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
        """Execute one reducer result in its declared order."""
        experiment_id = str(row.get("experiment_id") or "")
        sandbox_uid = str(row.get("sandbox_uid") or "")
        # The row this decision was reduced from names its owner; every write
        # below carries that name in its predicate (audit SAN-02).
        project_id = str(row.get("project_id") or "")
        current = row
        # A fenced intent that finds its claim reclaimed writes nothing. The
        # event must not outlive the write it describes: a `sandbox.released`
        # over a row somebody else now owns is a settlement that never happened.
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
        """Bring a row in line with reality. Read-only-safe (never provisions).

        - running → confirm liveness; mark terminated if the sandbox is gone;
          refresh the SSH endpoint if it moved.
        - provisioning → if a live job in this process owns it, leave it for
          the agent to keep polling (a live job owns the row at ANY age —
          Lambda boots legitimately run past the stale deadline); otherwise
          the job is gone (daemon restart) or wedged, so terminate whatever it
          left behind and mark failed once the provider confirms that worked.
          This is what guarantees a polling agent always reaches a settled
          state — terminal, or a visible `cleanup_pending`.
        """
        status = row.get("status")
        sandbox_uid = str(row.get("sandbox_uid") or "")
        if status in ACTIVE_SANDBOX_STATUSES and row.get("sandbox_id"):
            return self.apply(
                row=row,
                decision=reconcile_decision(
                    row=row,
                    # Row-qualified: a legacy id routed to today's default
                    # provider answers "not mine", and reconcile would read that
                    # as gone and terminalize a live, billing VM (audit SAN-06).
                    alive=self.liveness(row=row),
                ),
            )
        if status == "provisioning":
            experiment_id = str(row.get("experiment_id") or "")
            if self._job_is_live(
                experiment_id=experiment_id, sandbox_uid=sandbox_uid
            ):
                return row  # genuinely in flight — keep polling
            # The job may have JUST settled; re-read before declaring failure.
            fresh = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
            if fresh.get("status") != "provisioning":
                return self.reconcile(row=fresh)
            return self.apply(
                row=fresh,
                decision=reconcile_decision(
                    row=fresh,
                    alive=None,
                    job_live=False,
                    cleanup=self.terminate_vm(row=fresh),
                ),
            )
        return row

    def refresh_endpoint(self, *, row: dict[str, Any]) -> dict[str, Any]:
        """Re-read a live sandbox's SSH tunnel and persist it if it moved.

        Recovers the "sandbox alive ≠ tunnel endpoint still current" case
        (e.g. Modal relocates a sandbox): the new host/port is written back so
        the agent view + conn file hand out a working command.

        Strictly best-effort. A failure here — including a transient *local*
        resolver outage hitting the Modal control plane, the very thing the
        sbx dispatcher's retry/keepalive already absorbs — leaves the stored
        endpoint untouched and never breaks request/get. Only ``running`` rows
        with a sandbox id are probed.
        """
        if not row.get("sandbox_id") or row.get("status") not in ACTIVE_SANDBOX_STATUSES:
            return row
        # Ask the row's own provider: another provider's answer for a legacy
        # un-prefixed id would write a foreign host/port over a working one.
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
        if host == str(row.get("ssh_host") or "") and port == int(row.get("ssh_port") or 0):
            return row  # unchanged — the common case; avoid a needless write
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
        """Terminate every running sandbox whose expires_at deadline has passed.

        Idempotent and safe to call directly (tests do). Returns how many were
        reaped.
        """
        now_dt = now or datetime.now(tz=UTC)
        reaped = 0
        for row in self.repository.list_running_rows():
            expires_at = parse_iso(row.get("expires_at"))
            if expires_at is None or now_dt < expires_at:
                continue
            # Re-read: the sweep snapshot ages while earlier rows terminate
            # (provider calls take seconds each), and sandbox.extend races
            # exactly this window — a just-extended row must not be reaped
            # off the stale copy.
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
        """Terminate + mark one row (expiry and idle reaping share this).

        Returns False — parking the row as `cleanup_pending` for the retry
        sweep — when the VM could not be confirmed gone.
        """
        observed = self.observe_runs_before_terminal(row=row)
        outcome = self.terminate_vm(row=row)
        if outcome != "maybe_alive":
            # Stamp BEFORE the mark: the provider has confirmed the VM is gone,
            # so no new sentinel can appear and this read is final even if the
            # mark below raises and a later sweep completes it. A stamp on a
            # still-active row is inert — run_status checks status first.
            self.commit_runs_observation(row=row, observed=observed)
        self.apply(
            row=row,
            decision=reap_decision(
                row=row,
                outcome=outcome,
                event_type=event_type,
                payload_extra=payload_extra,
            ),
        )
        # maybe_alive parks the row for the retry sweep, so the read above
        # described a live box, not a final one.
        return outcome != "maybe_alive"

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
        """Confirm the VM is gone, then settle the row — or park it visibly.

        The shared ending for every pre-running path (failed provision, canceled
        provision, wedged provision). Returns the cleanup outcome so the caller
        can shape its own response.
        """
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
            ),
        )
        return outcome

    def retry_cleanup_pending(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Ask the provider again about every row whose deletion never confirmed.

        Unbounded in attempts, bounded in cadence: a possibly-billing VM is
        never given up on. ``ok`` is False while anything is still pending —
        that is the whole alerting mechanism, an outcome an operator can see.
        """
        now_dt = now or datetime.now(tz=UTC)
        rows = self.repository.list_rows_by_status(status=CLEANUP_PENDING_STATUS)
        confirmed = retried = 0
        for row in rows:
            attempts = cleanup_attempts(phase=row.get("phase"))
            last_attempt_at = parse_iso(row.get("updated_at"))
            if cleanup_inflight_token(phase=row.get("phase")):
                # Somebody holds this row. That is not a backoff question: the
                # row is worth looking at again only once its marker is past
                # the hard deadline, and then as a reclaim, not a retry.
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
        """Claim one cleanup attempt on the parked row the CALLER read.

        The manual `sandbox.release` path: an operator asking by hand may jump
        the retry backoff, but must not walk into an attempt already in flight.
        The in-flight marker is what tells it apart — a fresh read that shows
        somebody holds the row refuses here, and a stale snapshot names a
        marker the row no longer carries and is refused by the CAS. Rows in any
        other status are not claimed here: their single owner is established
        elsewhere (a live job, the reaper's own re-read).
        """
        return self._claim_cleanup(row=row)

    def claim_cleanup_due(self, *, row: dict[str, Any], now: datetime) -> CleanupClaim:
        """Claim one cleanup attempt for the retry sweep, if still due.

        Two independent guards. The in-flight marker is the exclusion: the
        sweep's workers arrive STAGGERED, so the second re-reads the row after
        the first has claimed it and blocked in the provider call, and what it
        reads back says outright that the attempt is taken. Being due is the
        cadence: a parked row is not asked about again until its backoff has
        elapsed, so the provider is not hammered.
        """
        return self._claim_cleanup(
            row=row,
            now=now,
            due_before=format_iso(
                _retry_cutoff(attempts=cleanup_attempts(phase=row.get("phase")), now=now)
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
        if str(row.get("status") or "") != CLEANUP_PENDING_STATUS:
            return CLEANUP_CLAIM_UNFENCED
        sandbox_uid = str(row.get("sandbox_uid") or "")
        if not sandbox_uid:
            return CLEANUP_CLAIM_UNFENCED
        now_dt = now or datetime.now(tz=UTC)
        phase = str(row.get("phase") or "")
        stale_before: str | None = None
        if cleanup_inflight_token(phase=phase):
            # The row says outright that an attempt is in flight. Nobody may
            # take it — not the sweep, not a manual release that re-read the
            # row AFTER the holder's claim and would otherwise see nothing but
            # a timestamp it cannot interpret.
            if not cleanup_claim_expired(
                claimed_at=parse_iso(row.get("updated_at")), now=now_dt
            ):
                return CLEANUP_CLAIM_REFUSED
            # Past the deadline the holder is presumed dead (or wedged past its
            # own bounded provider call) and the row is reclaimable — otherwise
            # one lost worker parks a possibly-billing VM forever. Reclaiming is
            # safe because the new token fences the old holder out: its late
            # write fails the CAS and settles nothing. The deadline replaces the
            # backoff here; the previous attempt is not going to report.
            stale_before = format_iso(cleanup_claim_cutoff(now=now_dt))
            due_before = None
        attempts = cleanup_attempts(phase=phase)
        token = new_cleanup_token()
        claimed = self.repository.claim_cleanup_attempt(
            sandbox_uid=sandbox_uid,
            phase=phase,
            attempts=attempts,
            expected_project_id=str(row.get("project_id") or ""),
            # One clock throughout: the instant this attempt is claimed is the
            # instant both the next backoff window and the in-flight deadline
            # are measured from.
            claimed_at=format_iso(now_dt),
            token=token,
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
        """One retry. True once the provider confirms the sandbox is gone."""
        now_dt = now or datetime.now(tz=UTC)
        experiment_id = str(row.get("experiment_id") or "")
        sandbox_uid = str(row.get("sandbox_uid") or "")
        project_id = str(row.get("project_id") or "")
        # Re-read first: the daemon loop and the cloud CleanupService sweep the
        # same pending rows, and a sibling worker may already have confirmed
        # this one gone while our snapshot aged. Only a row that is STILL
        # pending is worth another provider round-trip.
        if sandbox_uid:
            fresh = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
            if fresh.get("status") != CLEANUP_PENDING_STATUS:
                return True  # somebody else finished it; it is no longer pending
            row = fresh
            attempts = cleanup_attempts(phase=row.get("phase")) or attempts
        # ...and the re-read alone only proves it WAS pending — a straggler
        # even reads back the winner's own in-flight marker. Claim it, or a
        # sibling worker settles the same VM a second time and emits a second
        # confirmation for it.
        claim = self.claim_cleanup_due(row=row, now=now_dt)
        if not claim:
            return False
        # The attempt this worker owns, and the marker every write below asserts
        # so a reclaim of a wedged attempt cannot be undone by its late writer.
        attempts = claim.attempts or attempts + 1
        fence = claim.phase or None
        # The verdict this row was headed for before cleanup stalled: an origin
        # error means it was on its way to `failed`, not to a clean `terminated`.
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

"""Pure sandbox lifecycle decisions.

Provider calls and persistence belong to the lifecycle executor.  This module
only turns observed facts into an ordered set of effects plus the durable event
that describes the decision.  Keeping that split explicit makes the dangerous
rule easy to test: an unknown provider outcome never becomes a terminal row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


IntentKind = Literal[
    "mark_cleanup_pending",
    "mark_failed",
    "mark_terminated",
    "refresh_endpoint",
    "touch_alive",
]

# What one attempt to destroy a provider VM actually established.
CleanupOutcome = Literal["stopped", "gone", "maybe_alive"]

# Why a row was parked; carried into the durable event so the ledger shows
# which sweep hit the wall.
CLEANUP_PENDING_REASON = (
    "the provider did not confirm the sandbox was deleted, so it may still "
    "exist and bill; the cleanup sweep keeps retrying"
)


@dataclass(frozen=True, slots=True)
class ProviderLookup:
    """What the provider actually said when asked whether a sandbox exists.

    ``found``/``not_found`` are authoritative answers. ``unavailable`` means the
    provider could not be asked, and must never be read as "nothing is there"
    (audit SAN-06) — that collapse is how a live, billing VM ends up stranded
    behind a terminal row no sweep ever revisits.
    """

    kind: Literal["found", "not_found", "unavailable"]
    sandbox_id: str = ""
    error: str = ""


LOOKUP_NOT_FOUND = ProviderLookup(kind="not_found")


def lookup_found(sandbox_id: str) -> ProviderLookup:
    return ProviderLookup(kind="found", sandbox_id=str(sandbox_id))


def lookup_unavailable(error: str) -> ProviderLookup:
    return ProviderLookup(kind="unavailable", error=str(error)[:200])


@dataclass(frozen=True, slots=True)
class SideEffectIntent:
    kind: IntentKind
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    type: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    intents: tuple[SideEffectIntent, ...] = ()
    event: LifecycleEvent | None = None


def cleanup_pending_decision(
    *,
    row: Mapping[str, Any],
    trigger: str,
    error: str = "",
    attempts: int = 0,
) -> LifecycleDecision:
    """Park a row whose provider deletion was never confirmed.

    ``error`` is the verdict the row was headed for; it rides along so the
    retry that finally confirms the delete can finish the original journey
    (a failed provision must not quietly land as a clean ``terminated``).
    """
    sandbox_id = str(row.get("sandbox_id") or "")
    sandbox_uid = str(row.get("sandbox_uid") or "")
    return LifecycleDecision(
        intents=(
            SideEffectIntent(
                "mark_cleanup_pending",
                {
                    "reason": CLEANUP_PENDING_REASON,
                    "error": error,
                    "attempts": max(int(attempts), 1),
                },
            ),
        ),
        event=LifecycleEvent(
            "sandbox.cleanup_pending",
            {
                "sandbox_id": sandbox_id,
                "sandbox_uid": sandbox_uid,
                "trigger": trigger,
                "attempts": max(int(attempts), 1),
                "reason": CLEANUP_PENDING_REASON,
            },
        ),
    )


def settle_decision(
    *,
    row: Mapping[str, Any],
    outcome: CleanupOutcome,
    trigger: str,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    error: str = "",
) -> LifecycleDecision:
    """Settle a row once its cleanup attempt has finished.

    The one rule every caller shares: an unconfirmed delete never becomes a
    terminal row (audit SAN-05). ``error`` chooses the terminal verdict —
    non-empty means the row failed rather than simply ended.
    """
    if outcome == "maybe_alive":
        return cleanup_pending_decision(row=row, trigger=trigger, error=error)
    return LifecycleDecision(
        intents=(
            SideEffectIntent("mark_failed", {"error": error})
            if error
            else SideEffectIntent("mark_terminated", {}),
        ),
        event=LifecycleEvent(event_type, dict(payload or {})),
    )


def reconcile_decision(
    *,
    row: Mapping[str, Any],
    alive: bool | None,
    job_live: bool = False,
    cleanup: CleanupOutcome = "maybe_alive",
) -> LifecycleDecision:
    """Decide how one observed row should converge toward provider reality.

    ``cleanup`` is what the caller's termination attempt established for a
    wedged ``provisioning`` row; it defaults to the safe answer, so a caller
    that skipped the attempt parks the row instead of stranding a live VM.
    """
    status = str(row.get("status") or "")
    sandbox_id = str(row.get("sandbox_id") or "")
    sandbox_uid = str(row.get("sandbox_uid") or "")
    if status == "running" and sandbox_id:
        if alive is None:
            return LifecycleDecision()
        if alive:
            return LifecycleDecision(
                intents=(
                    SideEffectIntent("touch_alive", {}),
                    SideEffectIntent("refresh_endpoint", {}),
                )
            )
        return LifecycleDecision(
            intents=(SideEffectIntent("mark_terminated", {}),),
            event=LifecycleEvent(
                "sandbox.expired",
                {"sandbox_id": sandbox_id, "sandbox_uid": sandbox_uid},
            ),
        )
    if status == "provisioning" and not job_live:
        return settle_decision(
            row=row,
            outcome=cleanup,
            trigger="reconcile",
            event_type="sandbox.failed",
            payload={"error": "provisioning interrupted"},
            error="provisioning interrupted; call sandbox.request again",
        )
    return LifecycleDecision()


def reap_decision(
    *,
    row: Mapping[str, Any],
    outcome: CleanupOutcome,
    event_type: str,
    payload_extra: Mapping[str, Any] | None = None,
) -> LifecycleDecision:
    """Describe a reap after the provider termination attempt has completed."""
    sandbox_id = str(row.get("sandbox_id") or "")
    sandbox_uid = str(row.get("sandbox_uid") or "")
    extra = dict(payload_extra or {})
    if outcome == "maybe_alive":
        return cleanup_pending_decision(
            row=row, trigger=event_type.removeprefix("sandbox.")
        )
    return LifecycleDecision(
        intents=(SideEffectIntent("mark_terminated", {}),),
        event=LifecycleEvent(
            event_type,
            {
                "sandbox_id": sandbox_id,
                "sandbox_uid": sandbox_uid,
                "reaped": True,
                "expires_at": row.get("expires_at"),
                "stopped": outcome == "stopped",
                **extra,
            },
        ),
    )


def release_decision(
    *,
    row: Mapping[str, Any],
    outcome: CleanupOutcome,
    active_experiment_ids: list[str],
) -> LifecycleDecision:
    """Describe an explicitly confirmed release."""
    sandbox_id = str(row.get("sandbox_id") or "")
    sandbox_uid = str(row.get("sandbox_uid") or "")
    if outcome == "maybe_alive":
        return cleanup_pending_decision(row=row, trigger="release")
    return LifecycleDecision(
        intents=(SideEffectIntent("mark_terminated", {}),),
        event=LifecycleEvent(
            "sandbox.released",
            {
                "sandbox_id": sandbox_id,
                "sandbox_uid": sandbox_uid,
                "active_experiment_ids": list(active_experiment_ids),
                "stopped": outcome == "stopped",
            },
        ),
    )


__all__ = [
    "CLEANUP_PENDING_REASON",
    "LOOKUP_NOT_FOUND",
    "CleanupOutcome",
    "LifecycleDecision",
    "LifecycleEvent",
    "ProviderLookup",
    "SideEffectIntent",
    "cleanup_pending_decision",
    "lookup_found",
    "lookup_unavailable",
    "reap_decision",
    "reconcile_decision",
    "release_decision",
    "settle_decision",
]

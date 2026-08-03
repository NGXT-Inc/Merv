# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Quota admission and generation-ledger spend accounting.

Open generations bill through ``now``; absent quota rows are unlimited.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator

from ..kernel.state.store import BaseStateStore, row_to_dict
from ..kernel.utils import PermissionDeniedError, now_iso, parse_iso

# Tenant IDs cannot collide with this reserved global scope.
GLOBAL_SCOPE = "__global__"

# Sandbox rows that may already bill at the provider (mirrors
# running_sandbox_count): a provisioning row has launched or is about to,
# and cleanup_pending is an unconfirmed deletion that must stay accounted.
BILLABLE_STATUSES = ("provisioning", "running", "cleanup_pending")


def _next_utc_midnight(now: datetime) -> datetime:
    day = now.astimezone(UTC)
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)


def _hours(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() / 3600.0)


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    """Cost facts needed to admit one Sandbox request."""

    tenant_id: str
    time_limit_seconds: int
    price_usd_per_hour: float | None = None
    price_unknown_reason: str = ""
    # Per-provider daily-cap coordinates (Sandboxes → Configure). Empty
    # values skip the provider-day check — legacy callers stay untouched.
    project_id: str = ""
    provider: str = ""
    # Per-user per-provider daily-cap coordinates (migration 44). Empty
    # user_id or a billing_mode other than 'platform' skips the user check —
    # legacy/local callers and (phase-2) own-credential spend stay uncapped.
    user_id: str = ""
    billing_mode: str = ""


@dataclass(frozen=True)
class TenantQuota:
    """A tenant's ceilings. Every field None = unlimited for that dimension."""

    max_concurrent_sandboxes: int | None = None
    max_time_limit_seconds: int | None = None
    max_price_usd_per_hour: float | None = None
    gpu_hours_budget: float | None = None
    usd_budget: float | None = None


@dataclass(frozen=True, slots=True)
class _GenerationUsage:
    started: datetime
    ended: datetime
    hours: float
    price_usd_per_hour: float
    usd: float
    open: bool


def _row_effective_price(row: dict[str, Any]) -> float | None:
    """Best-known real hourly price of a live sandbox row, else None.

    The nullable quoted price (validated pre-launch) wins; the legacy
    NOT NULL price column is trusted only after provisioning completed AND
    the open generation marks it known — before that it is the meaningless
    zero floor, and after an allowed unknown-price completion it still is
    (finding 12).
    """
    quoted = row.get("quoted_price_usd_per_hour")
    if quoted is not None:
        return float(quoted)
    if (
        str(row.get("status")) == "running"
        and int(row.get("open_price_known") or 0) == 1
    ):
        return float(row.get("price_usd_per_hour") or 0.0)
    return None


def _generation_usage(
    generation: dict[str, Any], *, now: datetime
) -> _GenerationUsage | None:
    started = parse_iso(generation.get("started_at"))
    if started is None:
        return None
    recorded_end = parse_iso(generation.get("ended_at"))
    ended = recorded_end or now
    hours = max(0.0, (ended - started).total_seconds() / 3600.0)
    price = float(generation.get("price_usd_per_hour") or 0.0)
    return _GenerationUsage(
        started=started,
        ended=ended,
        hours=hours,
        price_usd_per_hour=price,
        usd=hours * price,
        open=recorded_end is None,
    )


class QuotaService:
    def __init__(self, *, store: BaseStateStore) -> None:
        self.store = store

    def get_quota(
        self, *, tenant_id: str, conn: Any | None = None
    ) -> TenantQuota | None:
        if conn is None:
            with closing(self.store.connect()) as owned:
                return self.get_quota(tenant_id=tenant_id, conn=owned)
        row = conn.execute(
            """
            SELECT max_concurrent_sandboxes, max_time_limit_seconds,
                   max_price_usd_per_hour, gpu_hours_budget, usd_budget
            FROM tenant_quotas WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchone()
        if row is None:
            return None
        data = row_to_dict(row=row) or {}
        return TenantQuota(
            max_concurrent_sandboxes=_int_or_none(data.get("max_concurrent_sandboxes")),
            max_time_limit_seconds=_int_or_none(data.get("max_time_limit_seconds")),
            max_price_usd_per_hour=_float_or_none(data.get("max_price_usd_per_hour")),
            gpu_hours_budget=_float_or_none(data.get("gpu_hours_budget")),
            usd_budget=_float_or_none(data.get("usd_budget")),
        )

    def set_quota(self, *, tenant_id: str, **fields: Any) -> None:
        columns = (
            "max_concurrent_sandboxes",
            "max_time_limit_seconds",
            "max_price_usd_per_hour",
            "gpu_hours_budget",
            "usd_budget",
        )
        values = {col: fields.get(col) for col in columns}
        with self.store.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM tenant_quotas WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
            if exists is None:
                conn.execute(
                    f"INSERT INTO tenant_quotas (tenant_id, {', '.join(columns)}) "
                    f"VALUES (?, {', '.join('?' for _ in columns)})",
                    (tenant_id, *(values[col] for col in columns)),
                )
            else:
                assignments = ", ".join(f"{col} = ?" for col in columns)
                conn.execute(
                    f"UPDATE tenant_quotas SET {assignments} WHERE tenant_id = ?",
                    (*(values[col] for col in columns), tenant_id),
                )

    def running_sandbox_count(
        self, *, tenant_id: str, conn: Any | None = None
    ) -> int:
        """Count provisioning and parked rows because either may already bill."""
        if conn is None:
            with closing(self.store.connect()) as owned:
                return self.running_sandbox_count(tenant_id=tenant_id, conn=owned)
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM sandboxes s
            JOIN projects p ON p.id = s.project_id
            WHERE p.tenant_id = ?
              AND s.status IN ('provisioning', 'running', 'cleanup_pending')
            """,
            (tenant_id,),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    # ---------- spend kill-switch ----------

    def set_kill_switch(
        self, *, scope: str, tripped: bool, reason: str = ""
    ) -> None:
        """Set a tenant or platform-wide provisioning halt."""
        with self.store.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM spend_kill_switches WHERE scope = ?", (scope,)
            ).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO spend_kill_switches "
                    "(scope, tripped, reason, tripped_at) VALUES (?, ?, ?, ?)",
                    (scope, 1 if tripped else 0, reason, now_iso() if tripped else None),
                )
            else:
                conn.execute(
                    "UPDATE spend_kill_switches "
                    "SET tripped = ?, reason = ?, tripped_at = ? WHERE scope = ?",
                    (1 if tripped else 0, reason, now_iso() if tripped else None, scope),
                )

    def kill_switch_tripped(
        self, *, scope: str, conn: Any | None = None
    ) -> dict[str, Any] | None:
        if conn is None:
            with closing(self.store.connect()) as owned:
                return self.kill_switch_tripped(scope=scope, conn=owned)
        row = conn.execute(
            "SELECT reason, tripped_at FROM spend_kill_switches "
            "WHERE scope = ? AND tripped = 1",
            (scope,),
        ).fetchone()
        if row is None:
            return None
        data = row_to_dict(row=row) or {}
        return {"reason": data.get("reason") or "", "tripped_at": data.get("tripped_at")}

    # ---------- running-total spend accounting ----------

    def tenant_generation_counters(
        self, *, tenant_id: str, now: datetime | None = None
    ) -> dict[str, float | int]:
        now_dt = now or datetime.now(tz=UTC)
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                "SELECT started_at, ended_at FROM sandbox_generations WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
        usages: list[_GenerationUsage] = []
        for row in rows:
            usage = _generation_usage(row_to_dict(row=row) or {}, now=now_dt)
            if usage is not None:
                usages.append(usage)
        hours = sum(usage.hours for usage in usages)
        return {"sandbox_generations": len(rows), "sandbox_hours": hours}

    def tenant_spend(
        self,
        *,
        tenant_id: str,
        now: datetime | None = None,
        conn: Any | None = None,
    ) -> dict[str, float]:
        """Bill open generations through ``now``.

        GPU-hours are generation wall time because the ledger has no GPU count.
        """
        now_dt = now or datetime.now(tz=UTC)
        if conn is None:
            with closing(self.store.connect()) as owned:
                return self.tenant_spend(
                    tenant_id=tenant_id, now=now_dt, conn=owned
                )
        rows = conn.execute(
            "SELECT price_usd_per_hour, started_at, ended_at "
            "FROM sandbox_generations WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchall()
        usd = 0.0
        gpu_hours = 0.0
        for row in rows:
            data = row_to_dict(row=row) or {}
            usage = _generation_usage(data, now=now_dt)
            if usage is None:
                continue
            gpu_hours += usage.hours
            usd += usage.usd
        return {"usd": usd, "gpu_hours": gpu_hours}

    def project_spend(
        self, *, project_id: str, now: datetime | None = None
    ) -> dict[str, Any]:
        """Group project spend by experiment, hardware, and UTC day.

        Zero-priced hours remain explicit instead of disappearing from totals.
        """
        now_dt = now or datetime.now(tz=UTC)
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                "SELECT experiment_id, instance_type, gpu, price_usd_per_hour, "
                "started_at, ended_at "
                "FROM sandbox_generations WHERE project_id = ? ORDER BY created_seq",
                (project_id,),
            ).fetchall()
        totals = {"usd": 0.0, "hours": 0.0, "unpriced_hours": 0.0}
        open_generations = 0
        burn_usd_per_hour = 0.0
        generations = 0
        by_experiment: dict[str, dict[str, Any]] = {}
        by_hardware: dict[tuple[str, str, float], dict[str, Any]] = {}
        daily: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = row_to_dict(row=row) or {}
            usage = _generation_usage(data, now=now_dt)
            if usage is None:
                continue
            generations += 1
            totals["usd"] += usage.usd
            totals["hours"] += usage.hours
            if usage.price_usd_per_hour <= 0:
                totals["unpriced_hours"] += usage.hours
            if usage.open:
                open_generations += 1
                burn_usd_per_hour += usage.price_usd_per_hour
            exp_id = str(data.get("experiment_id") or "")
            exp = by_experiment.setdefault(
                exp_id,
                {"experiment_id": exp_id, "usd": 0.0, "hours": 0.0, "generations": 0},
            )
            exp["usd"] += usage.usd
            exp["hours"] += usage.hours
            exp["generations"] += 1
            hw_key = (
                str(data.get("instance_type") or ""),
                str(data.get("gpu") or ""),
                usage.price_usd_per_hour,
            )
            hw = by_hardware.setdefault(
                hw_key,
                {
                    "instance_type": hw_key[0],
                    "gpu": hw_key[1],
                    "price_usd_per_hour": usage.price_usd_per_hour,
                    "usd": 0.0,
                    "hours": 0.0,
                    "generations": 0,
                },
            )
            hw["usd"] += usage.usd
            hw["hours"] += usage.hours
            hw["generations"] += 1
            for day, day_hours in _hours_by_utc_day(
                started=usage.started,
                ended=usage.ended,
            ):
                bucket = daily.setdefault(day, {"date": day, "usd": 0.0, "hours": 0.0})
                bucket["usd"] += day_hours * usage.price_usd_per_hour
                bucket["hours"] += day_hours
        def by_spend(entry: dict[str, Any]) -> tuple[float, float]:
            return (-entry["usd"], -entry["hours"])

        return {
            "total_usd": totals["usd"],
            "total_hours": totals["hours"],
            "unpriced_hours": totals["unpriced_hours"],
            "generations": generations,
            "open_generations": open_generations,
            "burn_usd_per_hour": burn_usd_per_hour,
            "by_experiment": sorted(by_experiment.values(), key=by_spend),
            "by_hardware": sorted(by_hardware.values(), key=by_spend),
            "daily": sorted(daily.values(), key=lambda bucket: bucket["date"]),
        }

    def check_admission(
        self,
        *,
        request: AdmissionRequest,
        conn: Any | None = None,
        _serialize: bool = False,
    ) -> None:
        """Check kill switches, cumulative budgets, then request ceilings."""
        if conn is None:
            with closing(self.store.connect()) as owned:
                return self.check_admission(request=request, conn=owned)
        # Serialize capped admissions so the second sees the first reservation.
        if _serialize:
            conn.execute(
                "UPDATE tenant_quotas SET tenant_id = tenant_id WHERE tenant_id = ?",
                (request.tenant_id,),
            )
            if request.project_id and request.provider:
                # Same trick for the project-provider cap row: two racing
                # admissions under one project cap must see each other.
                conn.execute(
                    "UPDATE sandbox_provider_settings SET provider = provider "
                    "WHERE project_id = ? AND provider = ?",
                    (request.project_id, request.provider),
                )
        self._check_kill_switch(scope=GLOBAL_SCOPE, label="platform", conn=conn)
        self._check_kill_switch(
            scope=request.tenant_id, label="tenant", conn=conn
        )
        self._check_provider_daily_limit(request=request, conn=conn)
        self._check_user_provider_daily_limit(
            request=request, conn=conn, serialize=_serialize
        )
        quota = self.get_quota(tenant_id=request.tenant_id, conn=conn)
        if quota is None:
            return
        self._check_budget(tenant_id=request.tenant_id, quota=quota, conn=conn)
        self._check_price_known(quota=quota, request=request)
        if (
            quota.max_concurrent_sandboxes is not None
            and self.running_sandbox_count(tenant_id=request.tenant_id, conn=conn)
            >= quota.max_concurrent_sandboxes
        ):
            raise PermissionDeniedError(
                "tenant sandbox quota reached: "
                f"{quota.max_concurrent_sandboxes} concurrent sandboxes",
                details={
                    "limit": quota.max_concurrent_sandboxes,
                    "quota": "max_concurrent_sandboxes",
                },
            )
        if (
            quota.max_time_limit_seconds is not None
            and request.time_limit_seconds > quota.max_time_limit_seconds
        ):
            raise PermissionDeniedError(
                "requested time_limit exceeds tenant ceiling "
                f"({quota.max_time_limit_seconds}s)",
                details={
                    "limit": quota.max_time_limit_seconds,
                    "requested": request.time_limit_seconds,
                    "quota": "max_time_limit_seconds",
                },
            )
        if (
            quota.max_price_usd_per_hour is not None
            and request.price_usd_per_hour is not None
            and request.price_usd_per_hour > quota.max_price_usd_per_hour
        ):
            raise PermissionDeniedError(
                "requested instance price exceeds tenant ceiling "
                f"(${quota.max_price_usd_per_hour}/hr)",
                details={
                    "limit": quota.max_price_usd_per_hour,
                    "requested": request.price_usd_per_hour,
                    "quota": "max_price_usd_per_hour",
                },
            )

    def check_lifetime_extension(
        self,
        *,
        tenant_id: str,
        total_time_limit_seconds: int,
        price_usd_per_hour: float | None = None,
        conn: Any | None = None,
        row: dict[str, Any] | None = None,
        added_seconds: int = 0,
    ) -> None:
        """Check extension policy without recounting an existing sandbox.

        When ``conn``/``row`` are given the caller holds the extension
        transaction: the user×provider cap is serialized and recomputed on
        that connection, charging the ROW's payer (of record), not the
        caller, for the added lease seconds priced to next UTC midnight.
        """
        if conn is None and row is not None:
            with closing(self.store.connect()) as owned:
                return self.check_lifetime_extension(
                    tenant_id=tenant_id,
                    total_time_limit_seconds=total_time_limit_seconds,
                    price_usd_per_hour=price_usd_per_hour,
                    conn=owned,
                    row=row,
                    added_seconds=added_seconds,
                )
        self._check_kill_switch(scope=GLOBAL_SCOPE, label="platform", conn=conn)
        self._check_kill_switch(scope=tenant_id, label="tenant", conn=conn)
        if row is not None and conn is not None:
            self._check_user_extension(
                row=row, added_seconds=added_seconds, conn=conn
            )
        quota = self.get_quota(tenant_id=tenant_id, conn=conn)
        if quota is None:
            return
        self._check_budget(tenant_id=tenant_id, quota=quota, conn=conn)
        if (
            quota.max_time_limit_seconds is not None
            and total_time_limit_seconds > quota.max_time_limit_seconds
        ):
            raise PermissionDeniedError(
                "extended sandbox lifetime exceeds tenant ceiling "
                f"({quota.max_time_limit_seconds}s)",
                details={
                    "limit": quota.max_time_limit_seconds,
                    "requested": total_time_limit_seconds,
                    "quota": "max_time_limit_seconds",
                },
            )
        if (
            quota.max_price_usd_per_hour is not None
            and price_usd_per_hour is not None
            and price_usd_per_hour > quota.max_price_usd_per_hour
        ):
            raise PermissionDeniedError(
                "running instance price exceeds tenant ceiling "
                f"(${quota.max_price_usd_per_hour}/hr)",
                details={
                    "limit": quota.max_price_usd_per_hour,
                    "requested": price_usd_per_hour,
                    "quota": "max_price_usd_per_hour",
                },
            )

    def _check_user_extension(
        self, *, row: dict[str, Any], added_seconds: int, conn: Any
    ) -> None:
        """Deny an extension whose added lease would push the payer of record
        past the user×provider daily cap. The row's CURRENT commitment is
        already inside committed burn; only the added window is new."""
        user_id = str(row.get("user_id") or "")
        provider = str(row.get("provider") or "")
        if (
            not user_id
            or not provider
            or str(row.get("billing_mode") or "") != "platform"
        ):
            return
        cap = self.store.resolve_provider_user_cap(
            provider=provider, user_id=user_id, conn=conn
        )
        if cap is None:
            return
        self.store.serialize_provider_user_cap(
            conn=conn, provider=provider, user_id=user_id
        )
        now = datetime.now(UTC)
        midnight = _next_utc_midnight(now)
        spent = self.user_provider_day_spend(
            user_id=user_id, provider=provider, conn=conn, now=now
        )
        committed = self.user_provider_committed_burn(
            user_id=user_id, provider=provider, conn=conn, now=now
        )
        open_known = conn.execute(
            "SELECT price_known FROM sandbox_generations "
            "WHERE sandbox_uid = ? AND ended_at IS NULL "
            "ORDER BY created_seq DESC LIMIT 1",
            (str(row.get("sandbox_uid") or ""),),
        ).fetchone()
        priced_row = {
            **row,
            "open_price_known": (
                open_known["price_known"] if open_known is not None else 0
            ),
        }
        price = _row_effective_price(priced_row)
        if price is None:
            # committed is already inf in this case; keep the denial explicit.
            price = float("inf")
        expires = parse_iso(row.get("expires_at")) or now
        added_start = max(now, expires)
        added_end = min(expires + timedelta(seconds=int(added_seconds)), midnight)
        added = _hours(added_start, added_end) * price if added_end > added_start else 0.0
        if spent + committed + added >= cap:
            raise PermissionDeniedError(
                f"extension denied: the payer's daily spend cap on {provider} "
                f"is exhausted (${spent:.2f} spent + ${committed:.2f} committed "
                f"+ ${added:.2f} added ≥ ${cap:.2f}; resets 00:00 UTC)",
                details={
                    "quota": "provider_user_daily_usd_limit",
                    "provider": provider,
                    "limit": cap,
                    "spent": spent,
                    "committed": committed,
                    "requested": added,
                    "resets_at": midnight.isoformat(),
                },
            )

    def check_final_quote(
        self,
        *,
        conn: Any,
        sandbox_uid: str,
        tenant_id: str,
        user_id: str,
        billing_mode: str,
        provider: str,
        time_limit_seconds: int,
        price: float | None,
    ) -> None:
        """Pre-launch revalidation of the adapter's final quote (on_quote).

        Replacement, not addition: the reservation row already exists, so the
        commitment scan excludes it and the final-price lease burn stands in
        for it — an unchanged quote reproduces the admission result. A None
        price under ANY dollar policy fails closed before launch.
        """
        cap = None
        if user_id and billing_mode == "platform" and provider:
            cap = self.store.resolve_provider_user_cap(
                provider=provider, user_id=user_id, conn=conn
            )
        if price is None:
            quota = self.get_quota(tenant_id=tenant_id, conn=conn)
            dollar_policy = cap is not None or (
                quota is not None
                and (
                    quota.usd_budget is not None
                    or quota.max_price_usd_per_hour is not None
                )
            )
            if dollar_policy:
                raise PermissionDeniedError(
                    f"provider {provider} no longer quotes a price for this "
                    "instance type and a spend policy applies — launch "
                    "aborted before any billable resource was created",
                    details={
                        "quota": "price_required_by_cost_policy",
                        "provider": provider,
                    },
                )
            return
        if cap is None:
            return
        self.store.serialize_provider_user_cap(
            conn=conn, provider=provider, user_id=user_id
        )
        now = datetime.now(UTC)
        spent = self.user_provider_day_spend(
            user_id=user_id, provider=provider, conn=conn, now=now
        )
        committed = self.user_provider_committed_burn(
            user_id=user_id,
            provider=provider,
            conn=conn,
            now=now,
            exclude_sandbox_uid=sandbox_uid,
        )
        lease_end = min(
            now + timedelta(seconds=int(time_limit_seconds)),
            _next_utc_midnight(now),
        )
        lease = _hours(now, lease_end) * float(price)
        if spent + committed + lease >= cap:
            raise PermissionDeniedError(
                f"final {provider} quote ${float(price):.2f}/hr would exceed "
                f"the payer's daily cap (${spent:.2f} spent + ${committed:.2f} "
                f"committed + ${lease:.2f} lease ≥ ${cap:.2f}) — launch "
                "aborted before any billable resource was created",
                details={
                    "quota": "provider_user_daily_usd_limit",
                    "provider": provider,
                    "limit": cap,
                    "spent": spent,
                    "committed": committed,
                    "requested": lease,
                    "resets_at": _next_utc_midnight(now).isoformat(),
                },
            )

    def _check_price_known(
        self, *, quota: "TenantQuota", request: AdmissionRequest
    ) -> None:
        """Require a known price only when a dollar policy needs one."""
        if not request.price_unknown_reason:
            return
        if quota.usd_budget is None and quota.max_price_usd_per_hour is None:
            return
        raise PermissionDeniedError(
            "this tenant has a spend policy, so a sandbox whose price cannot "
            f"be established will not be provisioned: {request.price_unknown_reason}. "
            "Pick an instance_type the provider catalog prices, publish a price "
            "for it, or clear usd_budget and max_price_usd_per_hour for this "
            "tenant to accept unpriced sandboxes.",
            details={
                "reason": request.price_unknown_reason,
                "quota": "price_required_by_cost_policy",
                "usd_budget": quota.usd_budget,
                "max_price_usd_per_hour": quota.max_price_usd_per_hour,
            },
        )

    def _check_kill_switch(
        self, *, scope: str, label: str, conn: Any | None = None
    ) -> None:
        tripped = self.kill_switch_tripped(scope=scope, conn=conn)
        if tripped is None:
            return
        reason = tripped.get("reason") or "spend halt"
        raise PermissionDeniedError(
            f"new sandbox provisioning is halted by the {label} spend "
            f"kill-switch: {reason}",
            details={
                "kill_switch": label,
                "scope": scope,
                "reason": reason,
                "tripped_at": tripped.get("tripped_at"),
            },
        )

    def provider_day_spend(
        self, *, project_id: str, provider: str, conn: Any | None = None
    ) -> float:
        """USD the project has spent on one provider since UTC midnight.

        Generation usage is clamped to today's window; open generations bill
        through now. Legacy rows with an empty provider tag never count
        toward a named provider's cap. Unpriced generations contribute $0 —
        the cap bounds *priced* spend, mirroring the ledger everywhere else.
        """
        if conn is None:
            with closing(self.store.connect()) as owned:
                return self.provider_day_spend(
                    project_id=project_id, provider=provider, conn=owned
                )
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = conn.execute(
            "SELECT started_at, ended_at, price_usd_per_hour "
            "FROM sandbox_generations "
            "WHERE project_id = ? AND provider = ? "
            "AND (ended_at IS NULL OR ended_at >= ?)",
            (project_id, provider, day_start.isoformat()),
        ).fetchall()
        spend = 0.0
        for row in rows:
            usage = _generation_usage(row_to_dict(row=row) or {}, now=now)
            if usage is None or usage.price_usd_per_hour <= 0:
                continue
            started = max(usage.started, day_start)
            if usage.ended <= started:
                continue
            hours = (usage.ended - started).total_seconds() / 3600.0
            spend += hours * usage.price_usd_per_hour
        return spend

    def user_provider_day_spend(
        self,
        *,
        user_id: str,
        provider: str,
        conn: Any | None = None,
        now: datetime | None = None,
    ) -> float:
        """USD one user has accrued on one provider's platform credentials
        since UTC midnight, across ALL projects.

        Same clamping as provider_day_spend; open generations bill through
        now. price_known=0 rows sum $0 here — enforcement treats them as
        exhausting the cap instead (see committed burn and the sweep).
        """
        if conn is None:
            with closing(self.store.connect()) as owned:
                return self.user_provider_day_spend(
                    user_id=user_id, provider=provider, conn=owned, now=now
                )
        now_dt = now or datetime.now(UTC)
        day_start = now_dt.astimezone(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        rows = conn.execute(
            "SELECT started_at, ended_at, price_usd_per_hour "
            "FROM sandbox_generations "
            "WHERE user_id = ? AND provider = ? AND billing_mode = 'platform' "
            "AND (ended_at IS NULL OR ended_at >= ?)",
            (user_id, provider, day_start.isoformat()),
        ).fetchall()
        spend = 0.0
        for row in rows:
            usage = _generation_usage(row_to_dict(row=row) or {}, now=now_dt)
            if usage is None or usage.price_usd_per_hour <= 0:
                continue
            started = max(usage.started, day_start)
            if usage.ended <= started:
                continue
            spend += _hours(started, usage.ended) * usage.price_usd_per_hour
        return spend

    def user_provider_committed_burn(
        self,
        *,
        user_id: str,
        provider: str,
        conn: Any,
        now: datetime | None = None,
        exclude_sandbox_uid: str = "",
    ) -> float:
        """USD the user's live sandboxes on this provider are still committed
        to burn before the next UTC midnight.

        Uniform fail-closed horizon: only a running row with a future
        expires_at gets that finite horizon; every other billable row —
        provisioning (boot is unbounded and live jobs are exempt from stale
        reaping), cleanup_pending, or a running row whose expiry passed but
        which the reaper has not confirmed dead — commits through midnight.
        A row whose price cannot be established commits infinity: no new
        spend is admitted while an unpriced box may be billing.

        ``exclude_sandbox_uid`` lets the pre-launch quote recheck replace its
        own row's commitment instead of double-counting it.
        """
        now_dt = now or datetime.now(UTC)
        midnight = _next_utc_midnight(now_dt)
        rows = conn.execute(
            "SELECT s.sandbox_uid, s.status, s.expires_at, "
            "       s.quoted_price_usd_per_hour, s.price_usd_per_hour, "
            "       (SELECT g.price_known FROM sandbox_generations g "
            "        WHERE g.sandbox_uid = s.sandbox_uid AND g.ended_at IS NULL "
            "        ORDER BY g.created_seq DESC LIMIT 1) AS open_price_known "
            "FROM sandboxes s "
            "WHERE s.user_id = ? AND s.provider = ? "
            "  AND s.billing_mode = 'platform' "
            f"  AND s.status IN ({', '.join('?' for _ in BILLABLE_STATUSES)})",
            (user_id, provider, *BILLABLE_STATUSES),
        ).fetchall()
        committed = 0.0
        for raw in rows:
            row = row_to_dict(row=raw) or {}
            if str(row.get("sandbox_uid") or "") == exclude_sandbox_uid:
                continue
            price = _row_effective_price(row)
            if price is None:
                return float("inf")
            expires = parse_iso(row.get("expires_at"))
            horizon = midnight
            if (
                str(row.get("status")) == "running"
                and expires is not None
                and expires > now_dt
            ):
                horizon = min(expires, midnight)
            committed += _hours(now_dt, horizon) * price
        return committed

    def _check_user_provider_daily_limit(
        self,
        *,
        request: AdmissionRequest,
        conn: Any,
        serialize: bool = False,
    ) -> None:
        """Commitment-based user×provider admission: deny once accrued spend
        plus committed burn plus the requested lease reaches the cap."""
        if (
            not request.user_id
            or not request.provider
            or request.billing_mode != "platform"
        ):
            return
        cap = self.store.resolve_provider_user_cap(
            provider=request.provider, user_id=request.user_id, conn=conn
        )
        if cap is None:
            return
        if serialize:
            self.store.serialize_provider_user_cap(
                conn=conn, provider=request.provider, user_id=request.user_id
            )
        if request.price_usd_per_hour is None:
            raise PermissionDeniedError(
                "a daily spend cap applies to this provider, so a sandbox "
                "whose price cannot be established will not be provisioned: "
                f"{request.price_unknown_reason or 'no catalog price'}. Pick "
                "an instance_type the provider catalog prices.",
                details={
                    "quota": "price_required_by_cost_policy",
                    "provider": request.provider,
                    "limit": cap,
                },
            )
        now = datetime.now(UTC)
        spent = self.user_provider_day_spend(
            user_id=request.user_id,
            provider=request.provider,
            conn=conn,
            now=now,
        )
        committed = self.user_provider_committed_burn(
            user_id=request.user_id,
            provider=request.provider,
            conn=conn,
            now=now,
        )
        lease_end = min(
            now + timedelta(seconds=int(request.time_limit_seconds)),
            _next_utc_midnight(now),
        )
        new_lease = _hours(now, lease_end) * float(request.price_usd_per_hour)
        if spent + committed + new_lease >= cap:
            raise PermissionDeniedError(
                f"daily user spend cap reached for provider {request.provider}: "
                f"${spent:.2f} spent + ${committed:.2f} committed "
                f"+ ${new_lease:.2f} requested ≥ ${cap:.2f} today (cap is "
                "enforced with a 1-hour grace on running sandboxes and resets "
                "at 00:00 UTC)",
                details={
                    "quota": "provider_user_daily_usd_limit",
                    "provider": request.provider,
                    "limit": cap,
                    "spent": spent,
                    "committed": committed,
                    "requested": new_lease,
                    "resets_at": _next_utc_midnight(now).isoformat(),
                },
            )

    def _check_provider_daily_limit(
        self, *, request: AdmissionRequest, conn: Any
    ) -> None:
        """Refuse NEW provisioning once today's provider spend reaches the
        project's cap (Sandboxes → Configure). Threshold semantics: already-
        running sandboxes keep billing; only new acquisition stops."""
        if not request.project_id or not request.provider:
            return
        limit = None
        for row in self.store.list_sandbox_provider_settings(
            project_id=request.project_id
        ):
            if row["provider"] == request.provider:
                limit = row["daily_usd_limit"]
                break
        if limit is None:
            return
        spend = self.provider_day_spend(
            project_id=request.project_id, provider=request.provider, conn=conn
        )
        if spend >= float(limit):
            raise PermissionDeniedError(
                f"daily spend limit reached for provider {request.provider}: "
                f"${spend:.2f} of ${float(limit):.2f} today — raise the limit "
                "under Sandboxes → Configure or wait for the UTC day to roll",
                details={
                    "limit": float(limit),
                    "spent": spend,
                    "quota": "provider_daily_usd_limit",
                    "provider": request.provider,
                },
            )

    def _check_budget(
        self, *, tenant_id: str, quota: "TenantQuota", conn: Any | None = None
    ) -> None:
        """Deny at the current ledger total; future runtime is not precharged."""
        if quota.gpu_hours_budget is None and quota.usd_budget is None:
            return
        spend = self.tenant_spend(tenant_id=tenant_id, conn=conn)
        if (
            quota.gpu_hours_budget is not None
            and spend["gpu_hours"] >= quota.gpu_hours_budget
        ):
            raise PermissionDeniedError(
                "tenant GPU-hour budget exhausted "
                f"({spend['gpu_hours']:.2f}/{quota.gpu_hours_budget} GPU-hours)",
                details={
                    "limit": quota.gpu_hours_budget,
                    "spent": spend["gpu_hours"],
                    "quota": "gpu_hours_budget",
                },
            )
        if quota.usd_budget is not None and spend["usd"] >= quota.usd_budget:
            raise PermissionDeniedError(
                "tenant USD spend budget exhausted "
                f"(${spend['usd']:.2f}/${quota.usd_budget})",
                details={
                    "limit": quota.usd_budget,
                    "spent": spend["usd"],
                    "quota": "usd_budget",
                },
            )


def _hours_by_utc_day(
    *, started: datetime, ended: datetime
) -> Iterator[tuple[str, float]]:
    cursor = started.astimezone(UTC)
    ended = ended.astimezone(UTC)
    while cursor < ended:
        next_midnight = datetime(
            cursor.year, cursor.month, cursor.day, tzinfo=UTC
        ) + timedelta(days=1)
        chunk_end = min(ended, next_midnight)
        yield cursor.date().isoformat(), (chunk_end - cursor).total_seconds() / 3600.0
        cursor = chunk_end


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)

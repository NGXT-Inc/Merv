# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Sandbox heartbeat policy plus the control-plane idle monitor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..kernel.utils import format_iso, parse_iso
from .observation import RunsObserver, SandboxMetrics, SandboxRunLedger
from .storage import SandboxStorage


class RowReaper(Protocol):
    """Terminate one sandbox row; True only once the provider confirms."""

    def __call__(
        self,
        *,
        row: dict[str, Any],
        event_type: str = ...,
        payload_extra: dict[str, Any] | None = ...,
    ) -> bool: ...


class SandboxIdlePolicy:
    """Pure idle decision from two usage samples."""

    max_cpu_cores = 0.01
    max_gpu_util_pct = 1.0
    max_network_bytes_per_second = 1024.0
    max_memory_bytes_per_second = 1024.0 * 1024.0

    def is_idle(
        self,
        *,
        current: dict[str, Any],
        previous: dict[str, Any] | None,
        elapsed_seconds: float,
        work_running: bool = False,
    ) -> bool:
        # Durable work evidence outranks sampled gauges.
        if work_running:
            return False
        if previous is None or elapsed_seconds <= 0:
            return False
        # Unmeasurable SSH (for example Modal) must not block reap forever.
        ssh = self._ssh_established(current)
        if ssh is not None and ssh != 0:
            return False
        cpu = _float((current.get("cpu") or {}).get("used_cores"))
        if cpu is None or cpu > self.max_cpu_cores:
            return False
        gpus = current.get("gpus") or []
        if not isinstance(gpus, list):
            return False
        for gpu in gpus:
            util = (
                _float((gpu or {}).get("util_pct")) if isinstance(gpu, dict) else None
            )
            if util is None or util > self.max_gpu_util_pct:
                return False
        net_rate = _rate(
            _network_bytes(current),
            _network_bytes(previous),
            elapsed_seconds,
            absolute=False,
        )
        if net_rate is None or net_rate > self.max_network_bytes_per_second:
            return False
        mem_rate = _rate(
            _memory_bytes(current),
            _memory_bytes(previous),
            elapsed_seconds,
            absolute=True,
        )
        return mem_rate is not None and mem_rate <= self.max_memory_bytes_per_second

    def next_idle_since(
        self,
        *,
        idle_since: datetime | None,
        now: datetime,
        is_idle: bool,
    ) -> datetime | None:
        return (idle_since or now) if is_idle else None

    def should_reap(
        self,
        *,
        idle_since: datetime | None,
        now: datetime,
        threshold_seconds: float,
    ) -> bool:
        return (
            threshold_seconds > 0
            and idle_since is not None
            and (now - idle_since).total_seconds() >= threshold_seconds
        )

    def _ssh_established(self, sample: dict[str, Any]) -> int | None:
        return _int((sample.get("network") or {}).get("ssh_established"))


class SandboxActivityPolicy:
    """Stricter renewal signal: busy enough to justify more runtime."""

    min_cpu_cores = 0.25
    min_gpu_util_pct = 10.0
    min_network_bytes_per_second = 64.0 * 1024.0
    min_memory_bytes_per_second = 25.0 * 1024.0 * 1024.0

    def is_active(
        self,
        *,
        current: dict[str, Any],
        previous: dict[str, Any] | None,
        elapsed_seconds: float,
        command: dict[str, Any] | None = None,
    ) -> bool:
        if command is not None and command.get("status") == "running":
            return True
        cpu = _float((current.get("cpu") or {}).get("used_cores"))
        if cpu is not None and cpu >= self.min_cpu_cores:
            return True
        gpus = current.get("gpus") or []
        if isinstance(gpus, list):
            for gpu in gpus:
                util = (
                    _float((gpu or {}).get("util_pct"))
                    if isinstance(gpu, dict)
                    else None
                )
                if util is not None and util >= self.min_gpu_util_pct:
                    return True
        if previous is None or elapsed_seconds <= 0:
            return False
        net_rate = _rate(
            _network_bytes(current),
            _network_bytes(previous),
            elapsed_seconds,
            absolute=False,
        )
        if net_rate is not None and net_rate >= self.min_network_bytes_per_second:
            return True
        mem_rate = _rate(
            _memory_bytes(current),
            _memory_bytes(previous),
            elapsed_seconds,
            absolute=True,
        )
        return mem_rate is not None and mem_rate >= self.min_memory_bytes_per_second

    def is_active_snapshot(
        self,
        *,
        snapshot: dict[str, Any] | None,
        command: dict[str, Any] | None = None,
    ) -> bool:
        if command is not None and command.get("status") == "running":
            return True
        if not isinstance(snapshot, dict):
            return False
        current = snapshot.get("metrics")
        if not isinstance(current, dict):
            return False
        previous = snapshot.get("previous_metrics")
        previous_at = parse_iso(snapshot.get("previous_sampled_at"))
        sampled_at = parse_iso(snapshot.get("sampled_at"))
        elapsed = (
            (sampled_at - previous_at).total_seconds()
            if sampled_at is not None and previous_at is not None
            else 0.0
        )
        return self.is_active(
            current=current,
            previous=previous if isinstance(previous, dict) else None,
            elapsed_seconds=elapsed,
            command=command,
        )


class SandboxHeartbeatMonitor:
    def __init__(
        self,
        *,
        storage: SandboxStorage,
        metrics: SandboxMetrics,
        runs: SandboxRunLedger,
        observer: RunsObserver,
        reap_row: RowReaper,
        policy: SandboxIdlePolicy | None = None,
    ) -> None:
        self.storage = storage
        self.metrics = metrics
        self.runs = runs
        self.observer = observer
        self.reap_row = reap_row
        self.policy = policy or SandboxIdlePolicy()

    def reap_idle(
        self, *, now: datetime | None = None, threshold_seconds: float
    ) -> int:
        if threshold_seconds <= 0:
            return 0
        now_dt = now or datetime.now(tz=UTC)
        reaped = 0
        for row in self.storage.list_running_rows():
            try:
                if self._tick_row(
                    row=row, now=now_dt, threshold_seconds=threshold_seconds
                ):
                    reaped += 1
            except Exception:  # noqa: BLE001 - heartbeat must never kill the loop
                continue
        return reaped

    def _tick_row(
        self, *, row: dict[str, Any], now: datetime, threshold_seconds: float
    ) -> bool:
        experiment_id = str(row.get("experiment_id") or "")
        sandbox_uid = str(row.get("sandbox_uid") or "")
        project_id = str(row.get("project_id") or "")
        if not experiment_id and not sandbox_uid:
            return False
        metrics = self._sample(row=row, experiment_id=experiment_id)
        if not isinstance(metrics, dict):
            return False
        previous_record = self.storage.heartbeat_snapshot(row=row)
        previous = (
            previous_record.get("metrics")
            if isinstance(previous_record, dict)
            else None
        )
        previous_at = parse_iso(
            previous_record.get("sampled_at")
            if isinstance(previous_record, dict)
            else None
        )
        idle_since = parse_iso(row.get("idle_since"))
        if not isinstance(previous, dict) or previous_at is None:
            self.storage.record_heartbeat(
                sandbox_uid=sandbox_uid,
                idle_since=None,
                snapshot=self._snapshot(metrics=metrics, now=now),
                expected_project_id=project_id,
            )
            return False
        is_idle = self.policy.is_idle(
            current=metrics,
            previous=previous,
            elapsed_seconds=(now - previous_at).total_seconds(),
            work_running=self._work_in_flight(
                row=row, now=now, max_age_seconds=threshold_seconds
            ),
        )
        next_idle_since = self.policy.next_idle_since(
            idle_since=idle_since, now=now, is_idle=is_idle
        )
        if not self.storage.record_heartbeat(
            sandbox_uid=sandbox_uid,
            expected_project_id=project_id,
            idle_since=format_iso(next_idle_since) if next_idle_since else None,
            snapshot=self._snapshot(
                metrics=metrics,
                now=now,
                previous_metrics=previous,
                previous_sampled_at=previous_at,
            ),
        ):
            return False
        if not self.policy.should_reap(
            idle_since=next_idle_since,
            now=now,
            threshold_seconds=threshold_seconds,
        ):
            return False
        # Provider calls age snapshots; reread before destructive action.
        fresh = self.storage.get_by_uid(sandbox_uid=sandbox_uid)
        if fresh.get("status") != "running" or parse_iso(fresh.get("idle_since")) is None:
            return False
        # Receipt silence after a failed read is ignorance, not an empty box.
        if not self._receipts_readable(row=fresh):
            return False
        if self._work_in_flight(
            row=fresh, now=now, max_age_seconds=threshold_seconds
        ):
            return False
        return bool(
            self.reap_row(
                row=fresh,
                event_type="sandbox.idle_reaped",
                payload_extra={
                    "idle_since": format_iso(next_idle_since),
                    "idle_seconds": int((now - next_idle_since).total_seconds()),
                    "threshold_seconds": int(threshold_seconds),
                },
            )
        )

    def _receipts_readable(self, *, row: dict[str, Any]) -> bool:
        """Veto reap when a supported receipt channel fails to answer."""
        if not str(row.get("sandbox_id") or ""):
            return True
        try:
            return bool(self.observer.observe_forced(row=row))
        except Exception:  # noqa: BLE001 — a failed read is not an empty box
            return False

    def _work_in_flight(
        self, *, row: dict[str, Any], now: datetime, max_age_seconds: float
    ) -> bool:
        if str(row.get("last_command_status") or "") == "running":
            return True
        try:
            return bool(
                self.runs.has_running_runs(
                    sandbox_uid=str(row.get("sandbox_uid") or ""),
                    fresh_since=(
                        now - timedelta(seconds=max_age_seconds)
                        if max_age_seconds > 0
                        else None
                    ),
                )
            )
        except Exception:  # noqa: BLE001 — an unreadable ledger never licenses a reap
            return True

    def _sample(
        self, *, row: dict[str, Any], experiment_id: str
    ) -> dict[str, Any] | None:
        result = self.metrics.sample_metrics(
            experiment_id=experiment_id,
            project_id=str(row.get("project_id") or ""),
            sandbox_uid=str(row.get("sandbox_uid") or ""),
        )
        metrics = result.get("metrics") if isinstance(result, dict) else None
        return metrics if isinstance(metrics, dict) else None

    def _snapshot(
        self,
        *,
        metrics: dict[str, Any],
        now: datetime,
        previous_metrics: dict[str, Any] | None = None,
        previous_sampled_at: datetime | None = None,
    ) -> dict[str, Any]:
        snapshot: dict[str, Any] = {"sampled_at": format_iso(now), "metrics": metrics}
        if previous_metrics is not None and previous_sampled_at is not None:
            snapshot["previous_metrics"] = previous_metrics
            snapshot["previous_sampled_at"] = format_iso(previous_sampled_at)
        return snapshot


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _memory_bytes(sample: dict[str, Any]) -> int | None:
    return _int((sample.get("memory") or {}).get("used_bytes"))


def _network_bytes(sample: dict[str, Any]) -> int | None:
    return _int((sample.get("network") or {}).get("bytes_total"))


def _rate(
    current: int | None,
    previous: int | None,
    elapsed_seconds: float,
    *,
    absolute: bool,
) -> float | None:
    if current is None or previous is None or elapsed_seconds <= 0:
        return None
    delta = current - previous
    if not absolute and delta < 0:
        return None
    return abs(delta if absolute else max(delta, 0)) / elapsed_seconds

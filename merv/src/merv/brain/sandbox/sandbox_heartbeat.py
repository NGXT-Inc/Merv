"""Sandbox heartbeat policy plus the control-plane idle monitor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Protocol

from .sandbox_support import parse_iso
from ..kernel.utils import format_iso


class RowReaper(Protocol):
    """Terminate one sandbox row; True only once the provider confirms."""

    def __call__(
        self,
        *,
        row: dict[str, Any],
        event_type: str = ...,
        payload_extra: dict[str, Any] | None = ...,
    ) -> bool: ...


class RunActivityProbe(Protocol):
    """The run ledger's durable 'is anything still in flight' read."""

    def __call__(
        self, *, sandbox_uid: str, fresh_since: datetime | None = ...
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
        # Durable evidence outranks sampled gauges: a merv_run receipt with no
        # exit code, or a command still in flight, means WORK IS RUNNING even
        # when every meter reads zero (audit SAN-07).
        if work_running:
            return False
        if previous is None or elapsed_seconds <= 0:
            return False
        # A live SSH session blocks idle; an UNMEASURABLE one (None — e.g. Modal
        # has no sshd, or ss/proc are absent) must not, or such boxes could never
        # reap. The activity signals below still guard genuinely-busy work.
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
    """Samples running sandboxes and delegates reap decisions to the policy."""

    def __init__(
        self,
        *,
        repository: Any,
        sample_metrics: Callable[..., dict[str, Any]],
        reap_row: RowReaper,
        policy: SandboxIdlePolicy | None = None,
        runs_active: RunActivityProbe | None = None,
    ) -> None:
        self.repository = repository
        self.sample_metrics = sample_metrics
        self.reap_row = reap_row
        self.policy = policy or SandboxIdlePolicy()
        # The run ledger's "is anything still in flight" probe; wired by the
        # composition so the monitor and the ledger stay peers.
        self.runs_active = runs_active

    def reap_idle(
        self, *, now: datetime | None = None, threshold_seconds: float
    ) -> int:
        if threshold_seconds <= 0:
            return 0
        now_dt = now or datetime.now(tz=UTC)
        reaped = 0
        for row in self.repository.list_running_rows():
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
        # The sweep read this row; its owner guards every write below (SAN-02).
        project_id = str(row.get("project_id") or "")
        if not experiment_id and not sandbox_uid:
            return False
        metrics = self._sample(row=row, experiment_id=experiment_id)
        if not isinstance(metrics, dict):
            return False
        previous_record = self.repository.heartbeat_snapshot(row=row)
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
            self.repository.record_heartbeat(
                experiment_id=experiment_id,
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
        self.repository.record_heartbeat(
            experiment_id=experiment_id,
            sandbox_uid=sandbox_uid,
            expected_project_id=project_id,
            idle_since=format_iso(next_idle_since) if next_idle_since else None,
            snapshot=self._snapshot(
                metrics=metrics,
                now=now,
                previous_metrics=previous,
                previous_sampled_at=previous_at,
            ),
        )
        if not self.policy.should_reap(
            idle_since=next_idle_since,
            now=now,
            threshold_seconds=threshold_seconds,
        ):
            return False
        # Re-read under a guard, as the expiry reaper does: the sweep snapshot
        # ages while earlier rows make provider calls, and a run launched or a
        # command started in that window must cancel this reap rather than lose
        # its box. Only a row that is STILL running and still idle is reaped.
        fresh = self.repository.get_by_uid(sandbox_uid=sandbox_uid)
        if fresh.get("status") != "running" or parse_iso(fresh.get("idle_since")) is None:
            return False
        if self._work_in_flight(
            row=fresh, now=now, max_age_seconds=threshold_seconds
        ):
            return False
        # Honest outcome: an unconfirmed provider deletion parks the row as
        # cleanup_pending and is NOT a reap (audit SAN-07).
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

    def _work_in_flight(
        self, *, row: dict[str, Any], now: datetime, max_age_seconds: float
    ) -> bool:
        """Durable evidence that this box is doing something, gauges aside."""
        if str(row.get("last_command_status") or "") == "running":
            return True
        if self.runs_active is None:
            return False
        try:
            return bool(
                self.runs_active(
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
        result = self.sample_metrics(
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

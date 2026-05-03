"""Long-running deployment supervisor helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import sleep
from typing import Protocol

from scalper_ai.deployment.alerts import AlertEvent, alerts_from_health_snapshot
from scalper_ai.deployment.health import HealthSnapshot, HealthStatus


class SupervisedRuntime(Protocol):
    """Runtime surface required by the supervisor."""

    def health_snapshot(self) -> HealthSnapshot:
        """Run runtime health checks."""

    def metrics_text(self) -> str:
        """Render runtime metrics."""


class AlertTransport(Protocol):
    """Alert transport surface used by the supervisor."""

    def write_alerts(self, alerts: tuple[AlertEvent, ...]) -> int:
        """Write alert events and return the number submitted."""


@dataclass(frozen=True)
class RuntimeSupervisorConfig:
    """Scheduling knobs for the runtime supervisor loop."""

    health_interval_seconds: float = 30.0
    reconciliation_interval_seconds: float = 60.0
    idle_sleep_seconds: float = 1.0
    alert_include_warnings: bool = True

    def __post_init__(self) -> None:
        if self.health_interval_seconds <= 0:
            raise ValueError("health_interval_seconds must be greater than zero.")
        if self.reconciliation_interval_seconds <= 0:
            raise ValueError("reconciliation_interval_seconds must be greater than zero.")
        if self.idle_sleep_seconds < 0:
            raise ValueError("idle_sleep_seconds must be non-negative.")


@dataclass(frozen=True)
class RuntimeSupervisorIteration:
    """One supervisor loop result."""

    checked_at: datetime
    health_due: bool
    reconciliation_due: bool
    snapshot: HealthSnapshot | None = None
    metrics_text: str = ""
    alerts: tuple[AlertEvent, ...] = ()
    alert_count: int = 0
    alert_error: str | None = None
    error: str | None = None

    @property
    def overall_status(self) -> HealthStatus | None:
        """Return the health status observed during this iteration."""

        return None if self.snapshot is None else self.snapshot.overall_status


class RuntimeSupervisor:
    """Small deterministic supervisor for scheduled health/reconciliation polling."""

    def __init__(
        self,
        runtime: SupervisedRuntime,
        *,
        config: RuntimeSupervisorConfig | None = None,
        alert_transport: AlertTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], object] | None = None,
    ) -> None:
        self._runtime = runtime
        self._config = config or RuntimeSupervisorConfig()
        self._alert_transport = alert_transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper or sleep
        self._last_health_at: datetime | None = None
        self._last_reconciliation_at: datetime | None = None

    def run_once(self) -> RuntimeSupervisorIteration:
        """Run one scheduled supervisor iteration."""

        checked_at = self._clock()
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("Supervisor clock must return timezone-aware datetimes.")

        health_due = self._is_due(
            self._last_health_at,
            interval_seconds=self._config.health_interval_seconds,
            now=checked_at,
        )
        reconciliation_due = self._is_due(
            self._last_reconciliation_at,
            interval_seconds=self._config.reconciliation_interval_seconds,
            now=checked_at,
        )
        if not health_due and not reconciliation_due:
            return RuntimeSupervisorIteration(
                checked_at=checked_at,
                health_due=False,
                reconciliation_due=False,
            )

        try:
            snapshot = self._runtime.health_snapshot()
            metrics_text = self._runtime.metrics_text()
        except Exception as exc:
            return RuntimeSupervisorIteration(
                checked_at=checked_at,
                health_due=health_due,
                reconciliation_due=reconciliation_due,
                error=str(exc),
            )

        alerts = alerts_from_health_snapshot(
            snapshot,
            include_warnings=self._config.alert_include_warnings,
        )
        alert_count = 0
        alert_error = None
        if self._alert_transport is not None and alerts:
            try:
                alert_count = self._alert_transport.write_alerts(alerts)
            except Exception as exc:
                alert_error = str(exc)

        self._last_health_at = checked_at
        if any(check.name == "execution_reconciliation" for check in snapshot.checks):
            self._last_reconciliation_at = checked_at
        return RuntimeSupervisorIteration(
            checked_at=checked_at,
            health_due=health_due,
            reconciliation_due=reconciliation_due,
            snapshot=snapshot,
            metrics_text=metrics_text,
            alerts=alerts,
            alert_count=alert_count,
            alert_error=alert_error,
        )

    def run_forever(
        self,
        *,
        max_iterations: int | None = None,
        max_runtime_seconds: float | None = None,
    ) -> tuple[RuntimeSupervisorIteration, ...]:
        """Run the supervisor loop until one configured bound is reached."""

        if max_iterations is not None and max_iterations <= 0:
            raise ValueError("max_iterations must be positive when provided.")
        if max_runtime_seconds is not None and max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive when provided.")

        iterations: list[RuntimeSupervisorIteration] = []
        started_at: datetime | None = None
        while True:
            iteration = self.run_once()
            iterations.append(iteration)
            if started_at is None:
                started_at = iteration.checked_at
            if max_iterations is not None and len(iterations) >= max_iterations:
                break
            if (
                max_runtime_seconds is not None
                and (iteration.checked_at - started_at).total_seconds() >= max_runtime_seconds
            ):
                break
            self._sleeper(self._config.idle_sleep_seconds)
        return tuple(iterations)

    @staticmethod
    def _is_due(
        previous_at: datetime | None,
        *,
        interval_seconds: float,
        now: datetime,
    ) -> bool:
        if previous_at is None:
            return True
        return (now - previous_at).total_seconds() >= interval_seconds

"""Long-running deployment supervisor helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import sleep
from typing import Protocol

from scalper_ai.deployment.health import HealthSnapshot, HealthStatus


class SupervisedRuntime(Protocol):
    """Runtime surface required by the supervisor."""

    def health_snapshot(self) -> HealthSnapshot:
        """Run runtime health checks."""

    def metrics_text(self) -> str:
        """Render runtime metrics."""


@dataclass(frozen=True)
class RuntimeSupervisorConfig:
    """Scheduling knobs for the runtime supervisor loop."""

    health_interval_seconds: float = 30.0
    reconciliation_interval_seconds: float = 60.0
    idle_sleep_seconds: float = 1.0

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
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], object] | None = None,
    ) -> None:
        self._runtime = runtime
        self._config = config or RuntimeSupervisorConfig()
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

        self._last_health_at = checked_at
        if any(check.name == "execution_reconciliation" for check in snapshot.checks):
            self._last_reconciliation_at = checked_at
        return RuntimeSupervisorIteration(
            checked_at=checked_at,
            health_due=health_due,
            reconciliation_due=reconciliation_due,
            snapshot=snapshot,
            metrics_text=metrics_text,
        )

    def run_forever(
        self,
        *,
        max_iterations: int | None = None,
    ) -> tuple[RuntimeSupervisorIteration, ...]:
        """Run the supervisor loop until max_iterations is reached, if provided."""

        if max_iterations is not None and max_iterations <= 0:
            raise ValueError("max_iterations must be positive when provided.")

        iterations: list[RuntimeSupervisorIteration] = []
        while max_iterations is None or len(iterations) < max_iterations:
            iterations.append(self.run_once())
            if max_iterations is None or len(iterations) < max_iterations:
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

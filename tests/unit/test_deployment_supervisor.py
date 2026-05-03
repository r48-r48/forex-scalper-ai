"""Tests for runtime supervisor scheduling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scalper_ai.deployment import (
    HealthCheckResult,
    HealthSnapshot,
    HealthStatus,
    RuntimeSupervisor,
    RuntimeSupervisorConfig,
)


def test_runtime_supervisor_runs_health_and_reconciliation_on_schedule() -> None:
    checked_at = datetime(2026, 4, 30, 13, 0, tzinfo=UTC)
    clock_values = [
        checked_at,
        checked_at + timedelta(seconds=5),
        checked_at + timedelta(seconds=31),
    ]
    runtime = _RecordingRuntime(checked_at)
    supervisor = RuntimeSupervisor(
        runtime,
        config=RuntimeSupervisorConfig(
            health_interval_seconds=30.0,
            reconciliation_interval_seconds=60.0,
            idle_sleep_seconds=0.0,
        ),
        clock=lambda: clock_values.pop(0),
        sleeper=lambda seconds: None,
    )

    first = supervisor.run_once()
    second = supervisor.run_once()
    third = supervisor.run_once()

    assert first.health_due is True
    assert first.reconciliation_due is True
    assert first.overall_status is HealthStatus.PASS
    assert second.health_due is False
    assert second.snapshot is None
    assert third.health_due is True
    assert third.reconciliation_due is False
    assert runtime.health_call_count == 2
    assert runtime.metrics_call_count == 2


def test_runtime_supervisor_reports_runtime_errors_without_raising() -> None:
    checked_at = datetime(2026, 4, 30, 13, 0, tzinfo=UTC)
    supervisor = RuntimeSupervisor(
        _BrokenRuntime(),
        clock=lambda: checked_at,
        sleeper=lambda seconds: None,
    )

    iteration = supervisor.run_once()

    assert iteration.error == "health failed"
    assert iteration.snapshot is None


def test_runtime_supervisor_routes_alerts_when_health_warns() -> None:
    checked_at = datetime(2026, 4, 30, 13, 0, tzinfo=UTC)
    transport = _RecordingAlertTransport()
    supervisor = RuntimeSupervisor(
        _WarningRuntime(checked_at),
        alert_transport=transport,
        clock=lambda: checked_at,
        sleeper=lambda seconds: None,
    )

    iteration = supervisor.run_once()

    assert len(iteration.alerts) == 1
    assert iteration.alert_count == 1
    assert iteration.alert_error is None
    assert iteration.alerts[0].rule_id == "reconciliation_drift"
    assert transport.alert_batches == [iteration.alerts]


def test_runtime_supervisor_can_skip_warning_alerts() -> None:
    checked_at = datetime(2026, 4, 30, 13, 0, tzinfo=UTC)
    transport = _RecordingAlertTransport()
    supervisor = RuntimeSupervisor(
        _WarningRuntime(checked_at),
        config=RuntimeSupervisorConfig(alert_include_warnings=False),
        alert_transport=transport,
        clock=lambda: checked_at,
        sleeper=lambda seconds: None,
    )

    iteration = supervisor.run_once()

    assert iteration.alerts == ()
    assert iteration.alert_count == 0
    assert transport.alert_batches == []


def test_runtime_supervisor_preserves_health_result_when_alert_transport_fails() -> None:
    checked_at = datetime(2026, 4, 30, 13, 0, tzinfo=UTC)
    supervisor = RuntimeSupervisor(
        _WarningRuntime(checked_at),
        alert_transport=_BrokenAlertTransport(),
        clock=lambda: checked_at,
        sleeper=lambda seconds: None,
    )

    iteration = supervisor.run_once()

    assert iteration.overall_status is HealthStatus.WARN
    assert len(iteration.alerts) == 1
    assert iteration.alert_count == 0
    assert iteration.alert_error == "alert transport failed"
    assert iteration.error is None


def test_runtime_supervisor_run_forever_honors_max_iterations() -> None:
    checked_at = datetime(2026, 4, 30, 13, 0, tzinfo=UTC)
    clock_values = [checked_at + timedelta(seconds=index) for index in range(3)]
    sleeps: list[float] = []
    supervisor = RuntimeSupervisor(
        _RecordingRuntime(checked_at),
        config=RuntimeSupervisorConfig(idle_sleep_seconds=0.25),
        clock=lambda: clock_values.pop(0),
        sleeper=lambda seconds: sleeps.append(seconds),
    )

    iterations = supervisor.run_forever(max_iterations=3)

    assert len(iterations) == 3
    assert sleeps == [0.25, 0.25]


def test_runtime_supervisor_run_forever_honors_max_runtime_seconds() -> None:
    checked_at = datetime(2026, 4, 30, 13, 0, tzinfo=UTC)
    clock_values = [
        checked_at,
        checked_at + timedelta(seconds=0.2),
        checked_at + timedelta(seconds=0.4),
        checked_at + timedelta(seconds=0.6),
    ]
    sleeps: list[float] = []
    supervisor = RuntimeSupervisor(
        _RecordingRuntime(checked_at),
        config=RuntimeSupervisorConfig(
            health_interval_seconds=0.1,
            reconciliation_interval_seconds=0.1,
            idle_sleep_seconds=0.05,
        ),
        clock=lambda: clock_values.pop(0),
        sleeper=lambda seconds: sleeps.append(seconds),
    )

    iterations = supervisor.run_forever(max_runtime_seconds=0.4)

    assert len(iterations) == 3
    assert iterations[-1].checked_at == checked_at + timedelta(seconds=0.4)
    assert sleeps == [0.05, 0.05]


def test_runtime_supervisor_run_forever_stops_on_first_bound_reached() -> None:
    checked_at = datetime(2026, 4, 30, 13, 0, tzinfo=UTC)
    clock_values = [checked_at + timedelta(seconds=index) for index in range(4)]
    supervisor = RuntimeSupervisor(
        _RecordingRuntime(checked_at),
        config=RuntimeSupervisorConfig(idle_sleep_seconds=0.0),
        clock=lambda: clock_values.pop(0),
        sleeper=lambda seconds: None,
    )

    iterations = supervisor.run_forever(max_iterations=2, max_runtime_seconds=100.0)

    assert len(iterations) == 2


def test_runtime_supervisor_rejects_non_positive_max_runtime_seconds() -> None:
    supervisor = RuntimeSupervisor(
        _RecordingRuntime(datetime(2026, 4, 30, 13, 0, tzinfo=UTC)),
    )

    with pytest.raises(ValueError, match="max_runtime_seconds"):
        supervisor.run_forever(max_runtime_seconds=0.0)


def test_runtime_supervisor_rejects_naive_clock() -> None:
    supervisor = RuntimeSupervisor(
        _RecordingRuntime(datetime(2026, 4, 30, 13, 0, tzinfo=UTC)),
        clock=lambda: datetime(2026, 4, 30, 13, 0),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        supervisor.run_once()


class _RecordingRuntime:
    def __init__(self, checked_at: datetime) -> None:
        self._checked_at = checked_at
        self.health_call_count = 0
        self.metrics_call_count = 0

    def health_snapshot(self) -> HealthSnapshot:
        self.health_call_count += 1
        return HealthSnapshot(
            service_name="scalper_ai_runtime",
            requested_mode="live",
            effective_mode="live",
            lifecycle_state="running",
            checked_at=self._checked_at,
            overall_status=HealthStatus.PASS,
            checks=(
                HealthCheckResult(
                    name="execution_reconciliation",
                    status=HealthStatus.PASS,
                    summary="Reconciliation found no broker/internal drift.",
                ),
            ),
        )

    def metrics_text(self) -> str:
        self.metrics_call_count += 1
        return "scalper_ai_runtime_up 1\n"


class _WarningRuntime:
    def __init__(self, checked_at: datetime) -> None:
        self._checked_at = checked_at

    def health_snapshot(self) -> HealthSnapshot:
        return HealthSnapshot(
            service_name="scalper_ai_runtime",
            requested_mode="live",
            effective_mode="live",
            lifecycle_state="running",
            checked_at=self._checked_at,
            overall_status=HealthStatus.WARN,
            checks=(
                HealthCheckResult(
                    name="execution_reconciliation",
                    status=HealthStatus.WARN,
                    summary="Reconciliation detected warning-level drift.",
                    details={"warning_count": 1},
                ),
            ),
        )

    def metrics_text(self) -> str:
        return "scalper_ai_runtime_up 1\n"


class _BrokenRuntime:
    def health_snapshot(self) -> HealthSnapshot:
        raise RuntimeError("health failed")

    def metrics_text(self) -> str:
        raise AssertionError("metrics must not be called when health fails")


class _RecordingAlertTransport:
    def __init__(self) -> None:
        self.alert_batches: list[tuple[object, ...]] = []

    def write_alerts(self, alerts: tuple[object, ...]) -> int:
        self.alert_batches.append(alerts)
        return len(alerts)


class _BrokenAlertTransport:
    def write_alerts(self, alerts: tuple[object, ...]) -> int:
        raise RuntimeError("alert transport failed")

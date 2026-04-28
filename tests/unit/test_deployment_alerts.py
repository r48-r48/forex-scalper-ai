"""Tests for deployment alert event rendering and JSONL transport."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from scalper_ai.deployment import (
    AlertSeverity,
    HealthCheckResult,
    HealthSnapshot,
    HealthStatus,
    JsonlAlertTransport,
    alerts_from_health_snapshot,
)


def test_alerts_from_health_snapshot_maps_live_broker_failure_to_critical() -> None:
    snapshot = _snapshot(
        effective_mode="live",
        overall_status=HealthStatus.FAIL,
        checks=(
            HealthCheckResult(
                name="broker_connectivity",
                status=HealthStatus.FAIL,
                summary="Broker connectivity check reports the broker dependency as unavailable.",
                details={"connected": False, "venue": "MT5"},
            ),
        ),
    )

    alerts = alerts_from_health_snapshot(snapshot)

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.rule_id == "broker_disconnect"
    assert alert.severity is AlertSeverity.CRITICAL
    assert alert.effective_mode == "live"
    assert alert.details["connected"] is False


def test_alerts_from_health_snapshot_can_skip_warnings() -> None:
    snapshot = _snapshot(
        effective_mode="paper",
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

    alerts = alerts_from_health_snapshot(snapshot)
    filtered = alerts_from_health_snapshot(snapshot, include_warnings=False)

    assert alerts[0].rule_id == "reconciliation_drift"
    assert alerts[0].severity is AlertSeverity.WARNING
    assert filtered == ()


def test_jsonl_alert_transport_appends_alert_events(tmp_path) -> None:
    snapshot = _snapshot(
        effective_mode="paper",
        overall_status=HealthStatus.WARN,
        checks=(
            HealthCheckResult(
                name="runtime_state",
                status=HealthStatus.WARN,
                summary="Runtime is running in degraded paper-safe mode.",
                details={"reason": "paper_fallback"},
            ),
        ),
    )
    alerts = alerts_from_health_snapshot(snapshot)
    output_path = tmp_path / "alerts.jsonl"
    transport = JsonlAlertTransport(output_path)

    written = transport.write_alerts(alerts)

    assert written == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["rule_id"] == "health_runtime_state"
    assert payload["severity"] == "warning"
    assert payload["raised_at"] == "2026-04-28T12:00:00+00:00"


def _snapshot(
    *,
    effective_mode: str,
    overall_status: HealthStatus,
    checks: tuple[HealthCheckResult, ...],
) -> HealthSnapshot:
    return HealthSnapshot(
        service_name="scalper_ai_runtime",
        requested_mode=effective_mode,
        effective_mode=effective_mode,
        lifecycle_state="running",
        checked_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
        overall_status=overall_status,
        checks=checks,
    )

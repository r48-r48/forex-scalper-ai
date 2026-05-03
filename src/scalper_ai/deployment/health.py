"""Operational health-check result models and registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class HealthStatus(StrEnum):
    """Ordered health severities."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class HealthCheckResult:
    """Result of one operational check."""

    name: str
    status: HealthStatus
    summary: str
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Health check name must be non-empty.")
        if not self.summary.strip():
            raise ValueError("Health check summary must be non-empty.")

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable payload."""

        return {
            "name": self.name,
            "status": self.status.value,
            "summary": self.summary,
            "details": {} if self.details is None else dict(self.details),
        }


@dataclass(frozen=True)
class HealthSnapshot:
    """Aggregated health report for one runtime."""

    service_name: str
    requested_mode: str
    effective_mode: str
    lifecycle_state: str
    checked_at: datetime
    overall_status: HealthStatus
    checks: tuple[HealthCheckResult, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable payload."""

        return {
            "service_name": self.service_name,
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "lifecycle_state": self.lifecycle_state,
            "checked_at": self.checked_at.isoformat(),
            "overall_status": self.overall_status.value,
            "checks": [check.to_dict() for check in self.checks],
        }


class HealthRegistry:
    """Runtime-bound registry of operational health checks."""

    def __init__(self, *, service_name: str, requested_mode: str) -> None:
        normalized_service_name = service_name.strip()
        normalized_requested_mode = requested_mode.strip()
        if not normalized_service_name:
            raise ValueError("service_name must be non-empty.")
        if not normalized_requested_mode:
            raise ValueError("requested_mode must be non-empty.")
        self._service_name = normalized_service_name
        self._requested_mode = normalized_requested_mode
        self._checks: list[tuple[str, Callable[[], HealthCheckResult]]] = []

    def register(self, name: str, check: Callable[[], HealthCheckResult]) -> None:
        """Register one named health check."""

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Health check name must be non-empty.")
        self._checks.append((normalized_name, check))

    def snapshot(
        self,
        *,
        effective_mode: str,
        lifecycle_state: str,
        checked_at: datetime,
    ) -> HealthSnapshot:
        """Run all registered checks and aggregate the result."""

        checks = tuple(check() for _, check in self._checks)
        overall_status = aggregate_health_status(checks)
        return HealthSnapshot(
            service_name=self._service_name,
            requested_mode=self._requested_mode,
            effective_mode=effective_mode,
            lifecycle_state=lifecycle_state,
            checked_at=checked_at,
            overall_status=overall_status,
            checks=checks,
        )


def aggregate_health_status(results: tuple[HealthCheckResult, ...]) -> HealthStatus:
    """Collapse many health results into one overall status."""

    if any(result.status is HealthStatus.FAIL for result in results):
        return HealthStatus.FAIL
    if any(result.status is HealthStatus.WARN for result in results):
        return HealthStatus.WARN
    return HealthStatus.PASS

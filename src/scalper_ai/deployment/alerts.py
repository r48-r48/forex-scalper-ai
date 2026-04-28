"""Alert event rendering and transports for deployment health surfaces."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from scalper_ai.deployment.health import HealthCheckResult, HealthSnapshot, HealthStatus


class AlertSeverity(StrEnum):
    """Operational alert severity."""

    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class AlertEvent:
    """One alert emitted from an operational runtime surface."""

    alert_id: str
    rule_id: str
    severity: AlertSeverity
    service_name: str
    requested_mode: str
    effective_mode: str
    raised_at: datetime
    source_check: str
    summary: str
    details: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.alert_id.strip():
            raise ValueError("alert_id must be non-empty.")
        if not self.rule_id.strip():
            raise ValueError("rule_id must be non-empty.")
        if self.raised_at.tzinfo is None or self.raised_at.utcoffset() is None:
            raise ValueError("raised_at must be timezone-aware.")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable alert payload."""

        return {
            "alert_id": self.alert_id,
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "service_name": self.service_name,
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "raised_at": self.raised_at.isoformat(),
            "source_check": self.source_check,
            "summary": self.summary,
            "details": dict(self.details),
        }


class JsonlAlertTransport:
    """Append alert events to a local JSONL file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def write_alerts(self, alerts: tuple[AlertEvent, ...]) -> int:
        """Append alerts and return the number of written events."""

        if not alerts:
            return 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            for alert in alerts:
                handle.write(json.dumps(alert.to_dict(), sort_keys=True))
                handle.write("\n")
        return len(alerts)


UrlOpener = Callable[[Request, float], Any]


class WebhookAlertTransport:
    """Post alert batches to an HTTP webhook endpoint."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 5.0,
        headers: Mapping[str, str] | None = None,
        opener: UrlOpener | None = None,
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Alert webhook URL must be an absolute HTTP(S) URL.")
        if timeout_seconds <= 0:
            raise ValueError("Alert webhook timeout must be positive.")
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._headers = dict(headers or {})
        self._opener = opener or _default_url_opener

    def write_alerts(self, alerts: tuple[AlertEvent, ...]) -> int:
        """Post alerts and return the number of submitted events."""

        if not alerts:
            return 0

        payload = json.dumps(_alert_batch_payload(alerts), sort_keys=True).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "scalper-ai-alerts/0.1",
            **self._headers,
        }
        request = Request(self._url, data=payload, headers=headers, method="POST")
        response = self._opener(request, self._timeout_seconds)
        try:
            status_code = _response_status_code(response)
            if status_code is not None and status_code >= 400:
                raise RuntimeError(f"Alert webhook returned HTTP {status_code}.")
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return len(alerts)


def alerts_from_health_snapshot(
    snapshot: HealthSnapshot,
    *,
    include_warnings: bool = True,
) -> tuple[AlertEvent, ...]:
    """Convert warning/failing health checks into alert events."""

    alerts: list[AlertEvent] = []
    for check in snapshot.checks:
        if check.status is HealthStatus.PASS:
            continue
        if check.status is HealthStatus.WARN and not include_warnings:
            continue
        rule_id = _rule_id_for(check)
        alerts.append(
            AlertEvent(
                alert_id=_alert_id(snapshot, rule_id, check),
                rule_id=rule_id,
                severity=_severity_for(snapshot, check),
                service_name=snapshot.service_name,
                requested_mode=snapshot.requested_mode,
                effective_mode=snapshot.effective_mode,
                raised_at=snapshot.checked_at,
                source_check=check.name,
                summary=check.summary,
                details={} if check.details is None else dict(check.details),
            )
        )
    return tuple(alerts)


def _rule_id_for(check: HealthCheckResult) -> str:
    if check.name == "broker_connectivity":
        return "broker_disconnect"
    if check.name == "execution_reconciliation":
        return "reconciliation_drift"
    if check.name == "execution_mode":
        return "execution_mode_degraded"
    return f"health_{check.name}"


def _severity_for(snapshot: HealthSnapshot, check: HealthCheckResult) -> AlertSeverity:
    if check.status is HealthStatus.FAIL:
        return AlertSeverity.CRITICAL
    if check.name == "broker_connectivity" and snapshot.effective_mode == "live":
        return AlertSeverity.CRITICAL
    if check.name == "execution_reconciliation" and snapshot.effective_mode == "live":
        return AlertSeverity.CRITICAL
    return AlertSeverity.WARNING


def _alert_id(snapshot: HealthSnapshot, rule_id: str, check: HealthCheckResult) -> str:
    return ":".join(
        (
            snapshot.service_name,
            snapshot.effective_mode,
            rule_id,
            check.name,
            snapshot.checked_at.isoformat(),
        )
    )


def _alert_batch_payload(alerts: tuple[AlertEvent, ...]) -> dict[str, Any]:
    return {
        "event_type": "scalper_ai_alert_batch",
        "alert_count": len(alerts),
        "alerts": [alert.to_dict() for alert in alerts],
    }


def _default_url_opener(request: Request, timeout_seconds: float) -> Any:
    return urlopen(request, timeout=timeout_seconds)


def _response_status_code(response: Any) -> int | None:
    status_code = getattr(response, "status", None)
    if status_code is None:
        getcode = getattr(response, "getcode", None)
        if callable(getcode):
            status_code = getcode()
    if status_code is None:
        return None
    return int(status_code)

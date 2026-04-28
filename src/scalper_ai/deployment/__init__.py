"""Deployment runtime bootstrap, health, and metrics helpers."""

from scalper_ai.deployment.alerts import (
    AlertEvent,
    AlertSeverity,
    JsonlAlertTransport,
    WebhookAlertTransport,
    alerts_from_health_snapshot,
)
from scalper_ai.deployment.entrypoints import bootstrap_runtime
from scalper_ai.deployment.health import HealthCheckResult, HealthSnapshot, HealthStatus
from scalper_ai.deployment.live_factory import (
    build_mt5_execution_adapter,
    build_mt5_terminal_client,
    resolve_live_adapter_factory,
)
from scalper_ai.deployment.metrics import MetricSample, MetricsRegistry
from scalper_ai.deployment.mt5_preflight import Mt5PreflightReport, build_mt5_preflight_report
from scalper_ai.deployment.runtime import DeploymentRuntime, RuntimeLifecycleState, RuntimeSummary

__all__ = [
    "AlertEvent",
    "AlertSeverity",
    "build_mt5_execution_adapter",
    "build_mt5_preflight_report",
    "build_mt5_terminal_client",
    "DeploymentRuntime",
    "HealthCheckResult",
    "HealthSnapshot",
    "HealthStatus",
    "JsonlAlertTransport",
    "MetricSample",
    "MetricsRegistry",
    "Mt5PreflightReport",
    "WebhookAlertTransport",
    "alerts_from_health_snapshot",
    "resolve_live_adapter_factory",
    "RuntimeLifecycleState",
    "RuntimeSummary",
    "bootstrap_runtime",
]

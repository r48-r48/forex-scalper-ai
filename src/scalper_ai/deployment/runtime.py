"""Deployment runtime bootstrap and safe service orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, cast

from scalper_ai.config import AppConfig
from scalper_ai.deployment.health import HealthCheckResult, HealthRegistry, HealthSnapshot, HealthStatus
from scalper_ai.deployment.metrics import MetricsRegistry
from scalper_ai.execution import (
    BrokerConnectivityProvider,
    BrokerConnectivitySnapshot,
    BrokerSnapshotProvider,
    ExecutionAdapter,
    ExecutionQuote,
    ExecutionRouter,
    ExecutionStateTracker,
    ExecutionUpdate,
    PaperExecutionAdapter,
    ReconciliationReport,
    build_snapshot_reconciliation_report,
)
from scalper_ai.domain import OrderIntent
from scalper_ai.utils import get_logger, resolve_repo_root


class RuntimeLifecycleState(str, Enum):
    """Lifecycle states for the deployment runtime."""

    CREATED = "created"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPED = "stopped"


@dataclass(frozen=True)
class RuntimeSummary:
    """Serializable runtime summary for entrypoints and tests."""

    service_name: str
    requested_mode: str
    effective_mode: str
    lifecycle_state: RuntimeLifecycleState
    started_at: Optional[datetime]
    startup_reason: Optional[str]
    execution_enabled: bool

    def to_dict(self) -> dict[str, object]:
        """Return a serializable payload."""

        return {
            "service_name": self.service_name,
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "lifecycle_state": self.lifecycle_state.value,
            "started_at": None if self.started_at is None else self.started_at.isoformat(),
            "startup_reason": self.startup_reason,
            "execution_enabled": self.execution_enabled,
        }


ReconciliationReportProvider = Callable[[], Optional[ReconciliationReport]]


class DeploymentRuntime:
    """Operational wrapper that exposes safe startup, health, and metrics surfaces."""

    def __init__(
        self,
        config: AppConfig,
        *,
        paper_adapter_factory: Optional[Callable[[], ExecutionAdapter]] = None,
        live_adapter_factory: Optional[Callable[[], ExecutionAdapter]] = None,
        broker_snapshot_provider: Optional[BrokerSnapshotProvider] = None,
        broker_connectivity_provider: Optional[BrokerConnectivityProvider] = None,
        reconciliation_report_provider: Optional[ReconciliationReportProvider] = None,
        live_confirmation_token: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self._service_name = config.monitoring.service_name or config.project_name
        self._paper_adapter_factory = paper_adapter_factory or PaperExecutionAdapter
        self._live_adapter_factory = live_adapter_factory
        self._broker_snapshot_provider = broker_snapshot_provider
        self._broker_connectivity_provider = broker_connectivity_provider
        self._reconciliation_report_provider = reconciliation_report_provider
        self._live_confirmation_token = live_confirmation_token
        self._logger = logger or get_logger(f"{config.logging.logger_name}.deployment")
        self._metrics = MetricsRegistry(service_name=self._service_name)
        self._health = HealthRegistry(service_name=self._service_name, requested_mode=config.runtime.mode)
        self._state_tracker = ExecutionStateTracker()
        self._router: Optional[ExecutionRouter] = None
        self._live_adapter: Optional[ExecutionAdapter] = None
        self._state = RuntimeLifecycleState.CREATED
        self._effective_mode = config.runtime.mode
        self._started_at: Optional[datetime] = None
        self._startup_reason: Optional[str] = None
        self._last_broker_connectivity_snapshot: Optional[BrokerConnectivitySnapshot] = None
        self._last_broker_connectivity_provider_error: Optional[str] = None
        self._last_reconciliation_report: Optional[ReconciliationReport] = None
        self._last_reconciliation_provider_error: Optional[str] = None
        self._register_default_health_checks()

    @property
    def metrics(self) -> MetricsRegistry:
        """Return the operational metrics registry."""

        return self._metrics

    @property
    def router(self) -> Optional[ExecutionRouter]:
        """Return the execution router when runtime execution is enabled."""

        return self._router

    @property
    def lifecycle_state(self) -> RuntimeLifecycleState:
        """Return the current runtime lifecycle state."""

        return self._state

    @property
    def requested_mode(self) -> str:
        """Return the configured runtime mode."""

        return self.config.runtime.mode

    @property
    def effective_mode(self) -> str:
        """Return the active runtime mode after safe fallbacks."""

        return self._effective_mode

    @property
    def execution_enabled(self) -> bool:
        """Return whether the runtime exposes an execution router."""

        return self._router is not None

    def start(self) -> RuntimeSummary:
        """Start the runtime with explicit safety checks and paper fallbacks."""

        if self._state in {RuntimeLifecycleState.RUNNING, RuntimeLifecycleState.DEGRADED}:
            raise RuntimeError("Runtime is already started.")

        self._state_tracker.clear()
        if self.config.deployment.create_directories_on_startup:
            for path in self.resolved_directories().values():
                path.mkdir(parents=True, exist_ok=True)

        self._started_at = datetime.now(timezone.utc)
        self._last_broker_connectivity_snapshot = None
        self._last_broker_connectivity_provider_error = None
        self._last_reconciliation_report = None
        self._last_reconciliation_provider_error = None
        self._metrics.increment("scalper_ai_runtime_start_total", requested_mode=self.requested_mode)
        self._activate_mode()
        self._metrics.set_gauge(
            "scalper_ai_runtime_up",
            1.0,
            requested_mode=self.requested_mode,
            effective_mode=self.effective_mode,
        )
        self._metrics.set_gauge(
            "scalper_ai_runtime_execution_enabled",
            1.0 if self.execution_enabled else 0.0,
            requested_mode=self.requested_mode,
            effective_mode=self.effective_mode,
        )
        self._logger.log(
            logging.WARNING if self._state is RuntimeLifecycleState.DEGRADED else logging.INFO,
            "Runtime started in %s mode (requested=%s).",
            self.effective_mode,
            self.requested_mode,
            extra={
                "component": "deployment",
                "event": "runtime_started",
            },
        )
        return self.summary()

    def stop(self) -> RuntimeSummary:
        """Stop the runtime and update operational metrics."""

        if self._state is RuntimeLifecycleState.STOPPED:
            return self.summary()

        self._close_live_adapter()
        self._state = RuntimeLifecycleState.STOPPED
        self._metrics.increment(
            "scalper_ai_runtime_shutdown_total",
            requested_mode=self.requested_mode,
            effective_mode=self.effective_mode,
        )
        self._metrics.set_gauge(
            "scalper_ai_runtime_up",
            0.0,
            requested_mode=self.requested_mode,
            effective_mode=self.effective_mode,
        )
        self._logger.info(
            "Runtime stopped.",
            extra={
                "component": "deployment",
                "event": "runtime_stopped",
            },
        )
        return self.summary()

    def summary(self) -> RuntimeSummary:
        """Return the current runtime summary."""

        return RuntimeSummary(
            service_name=self._service_name,
            requested_mode=self.requested_mode,
            effective_mode=self.effective_mode,
            lifecycle_state=self._state,
            started_at=self._started_at,
            startup_reason=self._startup_reason,
            execution_enabled=self.execution_enabled,
        )

    def health_snapshot(self) -> HealthSnapshot:
        """Run all registered health checks and return an aggregated snapshot."""

        if not self.config.monitoring.health_enabled:
            snapshot = HealthSnapshot(
                service_name=self._service_name,
                requested_mode=self.requested_mode,
                effective_mode=self.effective_mode,
                lifecycle_state=self._state.value,
                checked_at=datetime.now(timezone.utc),
                overall_status=HealthStatus.WARN,
                checks=(
                    HealthCheckResult(
                        name="health_surface",
                        status=HealthStatus.WARN,
                        summary="Health checks are disabled by configuration.",
                        details={"enabled": False},
                    ),
                ),
            )
        else:
            snapshot = self._health.snapshot(
                effective_mode=self.effective_mode,
                lifecycle_state=self._state.value,
                checked_at=datetime.now(timezone.utc),
            )

        warning_count = sum(result.status is HealthStatus.WARN for result in snapshot.checks)
        failure_count = sum(result.status is HealthStatus.FAIL for result in snapshot.checks)
        if self.config.monitoring.metrics_enabled:
            self._update_broker_connectivity_metrics()
            self._update_reconciliation_metrics()
        if self.config.monitoring.metrics_enabled and warning_count > 0:
            self._metrics.increment(
                "scalper_ai_healthcheck_warn_total",
                float(warning_count),
                requested_mode=self.requested_mode,
                effective_mode=self.effective_mode,
            )
        if self.config.monitoring.metrics_enabled and failure_count > 0:
            self._metrics.increment(
                "scalper_ai_healthcheck_fail_total",
                float(failure_count),
                requested_mode=self.requested_mode,
                effective_mode=self.effective_mode,
            )
        return snapshot

    def metrics_text(self) -> str:
        """Return a Prometheus-style metrics surface."""

        if not self.config.monitoring.metrics_enabled:
            return ""
        return self._metrics.render_prometheus()

    def require_execution_router(self) -> ExecutionRouter:
        """Return the execution router or fail loudly if the runtime has none."""

        if self._router is None:
            raise RuntimeError("Execution router is unavailable for the current runtime mode.")
        return self._router

    def submit_order(self, intent: OrderIntent, quote: ExecutionQuote) -> ExecutionUpdate:
        """Submit one order through the active execution router."""

        update = self.require_execution_router().submit_order(intent, quote)
        self._state_tracker.apply_update(update)
        if self.config.monitoring.metrics_enabled:
            self._metrics.increment(
                "scalper_ai_execution_orders_submitted_total",
                requested_mode=self.requested_mode,
                effective_mode=self.effective_mode,
                paper=str(intent.paper).lower(),
            )
        return update

    def process_quote(self, quote: ExecutionQuote) -> tuple[ExecutionUpdate, ...]:
        """Advance the execution router with a fresh quote."""

        updates = self.require_execution_router().process_quote(quote)
        self._state_tracker.apply_updates(updates)
        if self.config.monitoring.metrics_enabled and updates:
            self._metrics.increment(
                "scalper_ai_execution_updates_total",
                float(len(updates)),
                requested_mode=self.requested_mode,
                effective_mode=self.effective_mode,
            )
        return updates

    def resolved_directories(self) -> dict[str, Path]:
        """Return absolute storage directories for runtime startup checks."""

        repo_root = resolve_repo_root()
        return {
            "raw": (repo_root / self.config.directories.raw_dir).resolve(),
            "processed": (repo_root / self.config.directories.processed_dir).resolve(),
            "artifacts": (repo_root / self.config.directories.artifacts_dir).resolve(),
        }

    def _activate_mode(self) -> None:
        requested_mode = self.requested_mode
        self._router = None
        self._live_adapter = None
        self._effective_mode = requested_mode
        self._startup_reason = None

        if requested_mode == "research":
            self._state = RuntimeLifecycleState.RUNNING
            return

        if requested_mode == "paper":
            self._router = ExecutionRouter(paper_adapter=self._paper_adapter_factory())
            self._state = RuntimeLifecycleState.RUNNING
            return

        live_failure_reason = self._live_startup_blocker()
        if live_failure_reason is None:
            try:
                live_adapter = self._build_live_adapter()
            except Exception as exc:
                live_failure_reason = f"Live adapter startup failed: {exc}"
            else:
                self._live_adapter = live_adapter
                self._router = ExecutionRouter(
                    paper_adapter=self._paper_adapter_factory(),
                    live_adapter=live_adapter,
                )
                self._state = RuntimeLifecycleState.RUNNING
                return

        if self.config.deployment.fallback_to_paper_on_live_failure:
            self._router = ExecutionRouter(paper_adapter=self._paper_adapter_factory())
            self._effective_mode = "paper"
            self._state = RuntimeLifecycleState.DEGRADED
            self._startup_reason = live_failure_reason
            if self.config.monitoring.metrics_enabled:
                self._metrics.increment(
                    "scalper_ai_runtime_degraded_start_total",
                    requested_mode=self.requested_mode,
                    effective_mode=self._effective_mode,
                )
            return

        raise RuntimeError(live_failure_reason)

    def _build_live_adapter(self) -> ExecutionAdapter:
        if self._live_adapter_factory is None:
            raise RuntimeError("No live adapter factory is configured.")
        return self._live_adapter_factory()

    def _live_startup_blocker(self) -> Optional[str]:
        if not self.config.broker.live_enabled:
            return "Live runtime requested but broker.live_enabled is false."
        if (
            self.config.deployment.require_live_confirmation
            and self._live_confirmation_token != self.config.deployment.live_confirmation_phrase
        ):
            return "Live runtime confirmation phrase is missing or invalid."
        if not self.config.risk.kill_switch_enabled and not self.config.broker.allow_live_without_kill_switch:
            return "Live runtime requires risk.kill_switch_enabled=true."
        return None

    def _register_default_health_checks(self) -> None:
        self._health.register("runtime_state", self._runtime_state_check)
        self._health.register("storage_directories", self._storage_directory_check)
        self._health.register("execution_mode", self._execution_mode_check)
        self._health.register("broker_connectivity", self._broker_connectivity_check)
        self._health.register("execution_reconciliation", self._reconciliation_check)
        self._health.register("metrics_surface", self._metrics_surface_check)

    def _runtime_state_check(self) -> HealthCheckResult:
        if self._state is RuntimeLifecycleState.RUNNING:
            return HealthCheckResult(
                name="runtime_state",
                status=HealthStatus.PASS,
                summary="Runtime is running normally.",
                details={"lifecycle_state": self._state.value},
            )
        if self._state is RuntimeLifecycleState.DEGRADED:
            return HealthCheckResult(
                name="runtime_state",
                status=HealthStatus.WARN,
                summary="Runtime is running in degraded paper-safe mode.",
                details={"reason": self._startup_reason or "paper_fallback"},
            )
        if self._state is RuntimeLifecycleState.STOPPED:
            return HealthCheckResult(
                name="runtime_state",
                status=HealthStatus.WARN,
                summary="Runtime has already been stopped.",
                details={"lifecycle_state": self._state.value},
            )
        return HealthCheckResult(
            name="runtime_state",
            status=HealthStatus.FAIL,
            summary="Runtime has not been started yet.",
            details={"lifecycle_state": self._state.value},
        )

    def _storage_directory_check(self) -> HealthCheckResult:
        directories = self.resolved_directories()
        missing = [name for name, path in directories.items() if not path.exists() or not path.is_dir()]
        if missing:
            return HealthCheckResult(
                name="storage_directories",
                status=HealthStatus.FAIL,
                summary="One or more runtime directories are missing.",
                details={"missing": ",".join(sorted(missing))},
            )
        return HealthCheckResult(
            name="storage_directories",
            status=HealthStatus.PASS,
            summary="Runtime directories are present.",
            details={name: str(path) for name, path in directories.items()},
        )

    def _execution_mode_check(self) -> HealthCheckResult:
        if self._router is None:
            if self.effective_mode == "research":
                return HealthCheckResult(
                    name="execution_mode",
                    status=HealthStatus.PASS,
                    summary="Research mode intentionally runs without an execution router.",
                    details={"effective_mode": self.effective_mode},
                )
            return HealthCheckResult(
                name="execution_mode",
                status=HealthStatus.FAIL,
                summary="Execution router is unavailable.",
                details={"effective_mode": self.effective_mode},
            )

        if self.requested_mode == "live" and self.effective_mode != "live":
            return HealthCheckResult(
                name="execution_mode",
                status=HealthStatus.WARN,
                summary="Live request was downgraded to paper-safe execution.",
                details={"reason": self._startup_reason or "paper_fallback"},
            )

        return HealthCheckResult(
            name="execution_mode",
            status=HealthStatus.PASS,
            summary=f"{self.effective_mode.capitalize()} execution routing is active.",
            details={"effective_mode": self.effective_mode},
        )

    def _reconciliation_check(self) -> HealthCheckResult:
        self._last_reconciliation_report = None
        self._last_reconciliation_provider_error = None

        report_provider = self._resolved_reconciliation_report_provider()

        if report_provider is None:
            if self.effective_mode in {"research", "paper"}:
                return HealthCheckResult(
                    name="execution_reconciliation",
                    status=HealthStatus.PASS,
                    summary="No reconciliation provider is configured for the current safe mode.",
                    details={"configured": False, "effective_mode": self.effective_mode},
                )
            return HealthCheckResult(
                name="execution_reconciliation",
                status=HealthStatus.WARN,
                summary="Live-capable runtime has no reconciliation provider configured.",
                details={"configured": False, "effective_mode": self.effective_mode},
            )

        try:
            report = report_provider()
        except Exception as exc:
            self._last_reconciliation_provider_error = str(exc)
            return HealthCheckResult(
                name="execution_reconciliation",
                status=HealthStatus.FAIL,
                summary="Reconciliation provider raised an exception.",
                details={"configured": True, "error": str(exc)},
            )

        if report is None:
            if self.effective_mode in {"research", "paper"}:
                return HealthCheckResult(
                    name="execution_reconciliation",
                    status=HealthStatus.PASS,
                    summary="Reconciliation provider returned no broker snapshot in safe mode.",
                    details={"configured": True, "report_available": False},
                )
            return HealthCheckResult(
                name="execution_reconciliation",
                status=HealthStatus.WARN,
                summary="Reconciliation provider returned no broker snapshot for a live-capable runtime.",
                details={"configured": True, "report_available": False},
            )

        self._last_reconciliation_report = report
        if report.has_errors:
            status = HealthStatus.FAIL
            summary = "Reconciliation detected error-level drift between internal and broker state."
        elif report.warning_count > 0:
            status = HealthStatus.WARN
            summary = "Reconciliation detected warning-level drift between internal and broker state."
        else:
            status = HealthStatus.PASS
            summary = "Reconciliation found no broker/internal drift."

        issue_codes = ",".join(issue.code for issue in report.issues[:5])
        return HealthCheckResult(
            name="execution_reconciliation",
            status=status,
            summary=summary,
            details={
                "configured": True,
                "report_available": True,
                "error_count": report.error_count,
                "warning_count": report.warning_count,
                "issue_count": len(report.issues),
                "issue_codes": issue_codes,
            },
        )

    def _broker_connectivity_check(self) -> HealthCheckResult:
        self._last_broker_connectivity_snapshot = None
        self._last_broker_connectivity_provider_error = None

        provider = self._resolved_broker_connectivity_provider()
        if provider is None:
            if self.effective_mode in {"research", "paper"}:
                return HealthCheckResult(
                    name="broker_connectivity",
                    status=HealthStatus.PASS,
                    summary="No broker connectivity provider is required for the current safe mode.",
                    details={"configured": False, "effective_mode": self.effective_mode},
                )
            return HealthCheckResult(
                name="broker_connectivity",
                status=HealthStatus.WARN,
                summary="Live-capable runtime has no broker connectivity provider configured.",
                details={"configured": False, "effective_mode": self.effective_mode},
            )

        try:
            snapshot = provider.describe_broker_connectivity()
        except Exception as exc:
            self._last_broker_connectivity_provider_error = str(exc)
            return HealthCheckResult(
                name="broker_connectivity",
                status=HealthStatus.FAIL,
                summary="Broker connectivity provider raised an exception.",
                details={"configured": True, "error": str(exc)},
            )

        self._last_broker_connectivity_snapshot = snapshot
        age_seconds = snapshot.snapshot_age_seconds()
        details: dict[str, object] = {
            "configured": True,
            "connected": snapshot.connected,
            "venue": snapshot.venue,
            "checked_at": snapshot.checked_at.isoformat(),
            "stale_after_seconds": self.config.monitoring.broker_snapshot_stale_after_seconds,
        }
        if snapshot.last_snapshot_at is not None:
            details["last_snapshot_at"] = snapshot.last_snapshot_at.isoformat()
        if age_seconds is not None:
            details["snapshot_age_seconds"] = age_seconds
        if snapshot.latency_ms is not None:
            details["latency_ms"] = snapshot.latency_ms

        if not snapshot.connected:
            return HealthCheckResult(
                name="broker_connectivity",
                status=HealthStatus.FAIL if self.effective_mode == "live" else HealthStatus.WARN,
                summary="Broker connectivity check reports the broker dependency as unavailable.",
                details=details,
            )

        if (
            age_seconds is not None
            and age_seconds > self.config.monitoring.broker_snapshot_stale_after_seconds
        ):
            return HealthCheckResult(
                name="broker_connectivity",
                status=HealthStatus.WARN,
                summary="Broker connectivity is up, but the latest broker snapshot is stale.",
                details=details,
            )

        return HealthCheckResult(
            name="broker_connectivity",
            status=HealthStatus.PASS,
            summary="Broker connectivity looks healthy.",
            details=details,
        )

    def _metrics_surface_check(self) -> HealthCheckResult:
        if not self.config.monitoring.metrics_enabled:
            return HealthCheckResult(
                name="metrics_surface",
                status=HealthStatus.WARN,
                summary="Metrics surface is disabled by configuration.",
                details={"enabled": False},
            )
        rendered = self._metrics.render_prometheus()
        return HealthCheckResult(
            name="metrics_surface",
            status=HealthStatus.PASS,
            summary="Metrics surface is enabled.",
            details={"rendered": bool(rendered)},
        )

    def _update_reconciliation_metrics(self) -> None:
        labels = {
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
        }
        self._metrics.set_gauge(
            "scalper_ai_reconciliation_provider_configured",
            1.0 if self._resolved_reconciliation_report_provider() is not None else 0.0,
            **labels,
        )
        self._metrics.set_gauge(
            "scalper_ai_reconciliation_report_available",
            1.0 if self._last_reconciliation_report is not None else 0.0,
            **labels,
        )
        if self._last_reconciliation_report is None:
            self._metrics.set_gauge("scalper_ai_reconciliation_error_count", 0.0, **labels)
            self._metrics.set_gauge("scalper_ai_reconciliation_warning_count", 0.0, **labels)
            return

        self._metrics.set_gauge(
            "scalper_ai_reconciliation_error_count",
            float(self._last_reconciliation_report.error_count),
            **labels,
        )
        self._metrics.set_gauge(
            "scalper_ai_reconciliation_warning_count",
            float(self._last_reconciliation_report.warning_count),
            **labels,
        )

    def _update_broker_connectivity_metrics(self) -> None:
        labels = {
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
        }
        self._metrics.set_gauge(
            "scalper_ai_broker_connectivity_provider_configured",
            1.0 if self._resolved_broker_connectivity_provider() is not None else 0.0,
            **labels,
        )
        snapshot = self._last_broker_connectivity_snapshot
        self._metrics.set_gauge(
            "scalper_ai_broker_connected",
            1.0 if snapshot is not None and snapshot.connected else 0.0,
            **labels,
        )
        self._metrics.set_gauge(
            "scalper_ai_broker_snapshot_available",
            1.0 if snapshot is not None and snapshot.last_snapshot_at is not None else 0.0,
            **labels,
        )
        self._metrics.set_gauge(
            "scalper_ai_broker_snapshot_age_seconds",
            0.0 if snapshot is None or snapshot.snapshot_age_seconds() is None else snapshot.snapshot_age_seconds(),
            **labels,
        )
        self._metrics.set_gauge(
            "scalper_ai_broker_ping_latency_ms",
            0.0 if snapshot is None or snapshot.latency_ms is None else snapshot.latency_ms,
            **labels,
        )

    def _resolved_reconciliation_report_provider(self) -> Optional[ReconciliationReportProvider]:
        if self._reconciliation_report_provider is not None:
            return self._reconciliation_report_provider
        if self._resolved_broker_snapshot_provider() is None:
            return None
        return self._build_snapshot_report

    def _resolved_broker_connectivity_provider(self) -> Optional[BrokerConnectivityProvider]:
        if self._broker_connectivity_provider is not None:
            return self._broker_connectivity_provider
        snapshot_provider = self._as_broker_connectivity_provider(self._resolved_broker_snapshot_provider())
        if snapshot_provider is not None:
            return snapshot_provider
        return self._as_broker_connectivity_provider(self._live_adapter)

    def _build_snapshot_report(self) -> ReconciliationReport:
        snapshot_provider = self._resolved_broker_snapshot_provider()
        if snapshot_provider is None:
            raise RuntimeError("No broker snapshot provider is configured.")
        return build_snapshot_reconciliation_report(
            state_tracker=self._state_tracker,
            snapshot_provider=snapshot_provider,
            paper=self.effective_mode == "paper",
        )

    def _resolved_broker_snapshot_provider(self) -> Optional[BrokerSnapshotProvider]:
        if self._broker_snapshot_provider is not None:
            return self._broker_snapshot_provider
        return self._as_broker_snapshot_provider(self._live_adapter)

    def _as_broker_connectivity_provider(
        self,
        candidate: object | None,
    ) -> Optional[BrokerConnectivityProvider]:
        if candidate is None:
            return None
        provider_method = getattr(candidate, "describe_broker_connectivity", None)
        if not callable(provider_method):
            return None
        return cast(BrokerConnectivityProvider, candidate)

    def _as_broker_snapshot_provider(
        self,
        candidate: object | None,
    ) -> Optional[BrokerSnapshotProvider]:
        if candidate is None:
            return None
        list_orders_method = getattr(candidate, "list_broker_orders", None)
        list_positions_method = getattr(candidate, "list_broker_positions", None)
        if not callable(list_orders_method) or not callable(list_positions_method):
            return None
        return cast(BrokerSnapshotProvider, candidate)

    def _close_live_adapter(self) -> None:
        if self._live_adapter is None:
            return
        close_method = getattr(self._live_adapter, "close", None)
        if callable(close_method):
            close_method()

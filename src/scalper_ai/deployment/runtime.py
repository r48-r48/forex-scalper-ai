"""Deployment runtime bootstrap and safe service orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from scalper_ai.backtesting.accounting import calculate_equity, mark_position
from scalper_ai.config import AppConfig
from scalper_ai.deployment.health import (
    HealthCheckResult,
    HealthRegistry,
    HealthSnapshot,
    HealthStatus,
)
from scalper_ai.deployment.metrics import MetricsRegistry
from scalper_ai.domain import OrderIntent, OrderSide, OrderType, PositionState
from scalper_ai.execution import (
    BrokerConnectivityProvider,
    BrokerConnectivitySnapshot,
    BrokerSnapshotProvider,
    ExecutionAdapter,
    ExecutionOrder,
    ExecutionOrderStatus,
    ExecutionQuote,
    ExecutionRouter,
    ExecutionStateStore,
    ExecutionStateTracker,
    ExecutionUpdate,
    KillSwitchScope,
    KillSwitchState,
    PaperExecutionAdapter,
    ReconciliationReport,
    build_snapshot_reconciliation_report,
)
from scalper_ai.journal import JournalEvent, JournalEventType
from scalper_ai.risk import RiskContext, RiskDecision, RiskDecisionStatus, RiskEngine, RiskLimits
from scalper_ai.services import OmsOrderRecord, OmsOrderStatus, transition_order
from scalper_ai.utils import get_logger, resolve_repo_root


class RuntimeLifecycleState(StrEnum):
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
    started_at: datetime | None
    startup_reason: str | None
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


ReconciliationReportProvider = Callable[[], ReconciliationReport | None]
RiskContextProvider = Callable[[OrderIntent, ExecutionQuote, ExecutionStateTracker], RiskContext]

_POSITION_PROTECTION_ISSUE_CODES = frozenset(
    {
        "position_stop_loss_missing",
        "position_stop_loss_mismatch",
        "position_stop_loss_ambiguous",
        "position_take_profit_missing",
        "position_take_profit_mismatch",
        "position_take_profit_ambiguous",
    }
)


class JournalWriterProtocol(Protocol):
    """Minimal journal writer surface used by the runtime audit hooks."""

    def write(self, event: JournalEvent) -> object:
        """Persist one journal event."""


class DeploymentRuntime:
    """Operational wrapper that exposes safe startup, health, and metrics surfaces."""

    def __init__(
        self,
        config: AppConfig,
        *,
        paper_adapter_factory: Callable[[], ExecutionAdapter] | None = None,
        live_adapter_factory: Callable[[], ExecutionAdapter] | None = None,
        broker_snapshot_provider: BrokerSnapshotProvider | None = None,
        broker_connectivity_provider: BrokerConnectivityProvider | None = None,
        reconciliation_report_provider: ReconciliationReportProvider | None = None,
        risk_engine: RiskEngine | None = None,
        risk_context_provider: RiskContextProvider | None = None,
        journal_writer: JournalWriterProtocol | None = None,
        state_store: ExecutionStateStore | None = None,
        live_confirmation_token: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self._service_name = config.monitoring.service_name or config.project_name
        self._paper_adapter_factory = paper_adapter_factory or PaperExecutionAdapter
        self._live_adapter_factory = live_adapter_factory
        self._broker_snapshot_provider = broker_snapshot_provider
        self._broker_connectivity_provider = broker_connectivity_provider
        self._reconciliation_report_provider = reconciliation_report_provider
        self._risk_engine = risk_engine or RiskEngine(RiskLimits.from_risk_config(config.risk))
        self._risk_context_provider = risk_context_provider
        self._journal_writer = journal_writer
        self._state_store = state_store
        self._live_confirmation_token = live_confirmation_token
        self._logger = logger or get_logger(f"{config.logging.logger_name}.deployment")
        self._metrics = MetricsRegistry(service_name=self._service_name)
        self._health = HealthRegistry(
            service_name=self._service_name,
            requested_mode=config.runtime.mode,
        )
        self._state_tracker = ExecutionStateTracker()
        self._oms_records: dict[str, OmsOrderRecord] = {}
        self._journal_events: list[JournalEvent] = []
        self._last_cash_balance_by_route: dict[bool, float] = {}
        self._last_equity_by_route: dict[bool, float] = {}
        self._session_kill_switch_enabled = False
        self._symbol_kill_switches: set[str] = set()
        self._recovered_execution_update_count = 0
        self._recovered_oms_record_count = 0
        self._router: ExecutionRouter | None = None
        self._live_adapter: ExecutionAdapter | None = None
        self._state = RuntimeLifecycleState.CREATED
        self._effective_mode = config.runtime.mode
        self._started_at: datetime | None = None
        self._startup_reason: str | None = None
        self._last_broker_connectivity_snapshot: BrokerConnectivitySnapshot | None = None
        self._last_broker_connectivity_provider_error: str | None = None
        self._last_reconciliation_report: ReconciliationReport | None = None
        self._last_reconciliation_provider_error: str | None = None
        self._register_default_health_checks()

    @property
    def metrics(self) -> MetricsRegistry:
        """Return the operational metrics registry."""

        return self._metrics

    @property
    def router(self) -> ExecutionRouter | None:
        """Return the execution router when runtime execution is enabled."""

        return self._router

    @property
    def journal_events(self) -> tuple[JournalEvent, ...]:
        """Return journal events recorded by the runtime in process memory."""

        return tuple(self._journal_events)

    @property
    def oms_records(self) -> tuple[OmsOrderRecord, ...]:
        """Return latest OMS lifecycle records keyed by intent id."""

        return tuple(sorted(self._oms_records.values(), key=lambda record: record.intent.intent_id))

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

        if self.config.deployment.create_directories_on_startup:
            for path in self.resolved_directories().values():
                path.mkdir(parents=True, exist_ok=True)

        self._reset_recoverable_state()
        self._recover_state_from_store()
        self._started_at = datetime.now(UTC)
        self._last_broker_connectivity_snapshot = None
        self._last_broker_connectivity_provider_error = None
        self._last_reconciliation_report = None
        self._last_reconciliation_provider_error = None
        self._metrics.increment(
            "scalper_ai_runtime_start_total",
            requested_mode=self.requested_mode,
        )
        try:
            self._activate_mode()
            self._block_unsafe_recovered_startup()
        except Exception:
            self._close_live_adapter()
            self._router = None
            self._live_adapter = None
            self._state = RuntimeLifecycleState.CREATED
            raise
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
        self._metrics.set_gauge(
            "scalper_ai_runtime_recovered_execution_updates",
            float(self._recovered_execution_update_count),
            requested_mode=self.requested_mode,
            effective_mode=self.effective_mode,
        )
        self._metrics.set_gauge(
            "scalper_ai_runtime_recovered_oms_records",
            float(self._recovered_oms_record_count),
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
                checked_at=datetime.now(UTC),
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
                checked_at=datetime.now(UTC),
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
        """Submit one order through mandatory risk, OMS, journal, and routing gates."""

        risk_context = self._build_risk_context(intent, quote)
        risk_decision = self._risk_engine.evaluate_order(intent, risk_context)
        self._record_risk_decision(risk_decision)

        oms_record = OmsOrderRecord.new(intent)
        if not risk_decision.accepted:
            rejected_record = transition_order(
                oms_record,
                OmsOrderStatus.REJECTED,
                updated_at=risk_decision.checked_at,
                rejection_reason=risk_decision.reason or "risk_rejected",
            )
            self._remember_oms_record(rejected_record)
            self._record_oms_event(rejected_record, "risk_rejected")
            update = self._build_risk_rejected_update(intent, quote, risk_context, risk_decision)
            self._state_tracker.apply_update(update)
            self._remember_account_state(update)
            self._record_order_response(update, causation_id=rejected_record.intent.intent_id)
            self._increment_rejected_metric(intent)
            return update

        return self._submit_risk_approved_order(intent, quote, risk_decision)

    def flatten_unprotected_positions(
        self,
        quotes_by_symbol: Mapping[str, ExecutionQuote],
        *,
        approval_token: str,
        created_at: datetime | None = None,
        strategy_id: str = "runtime-position-protection",
    ) -> tuple[ExecutionUpdate, ...]:
        """Submit approved reduce-only flatten orders for unprotected live positions."""

        self._validate_position_flatten_approval(approval_token)
        if self.effective_mode != "live":
            raise RuntimeError(
                "Approved position-protection flattening is only available in live mode."
            )

        snapshot_provider = self._resolved_broker_snapshot_provider()
        if snapshot_provider is None:
            raise RuntimeError("Cannot flatten unprotected positions without broker snapshots.")

        report = self._last_reconciliation_report
        if report is None:
            report_provider = self._resolved_reconciliation_report_provider()
            if report_provider is None:
                raise RuntimeError("Cannot flatten unprotected positions without reconciliation.")
            report = report_provider()
            if report is None:
                raise RuntimeError(
                    "Cannot flatten unprotected positions without a reconciliation report."
                )
            self._last_reconciliation_report = report
        self._activate_reconciliation_fail_safe(
            report,
            default_reason="position_protection_reconciliation_failed",
        )

        target_symbols = _position_protection_issue_symbols(report)
        if not target_symbols:
            return ()

        timestamp = created_at or datetime.now(UTC)
        updates: list[ExecutionUpdate] = []
        for index, broker_position in enumerate(
            sorted(snapshot_provider.list_broker_positions(), key=lambda item: item.symbol),
            start=1,
        ):
            if broker_position.symbol not in target_symbols:
                continue
            if abs(float(broker_position.net_quantity)) <= 1e-9:
                continue
            quote = quotes_by_symbol.get(broker_position.symbol)
            if quote is None:
                raise KeyError(
                    f"Missing quote for approved flatten symbol: {broker_position.symbol}"
                )
            if quote.symbol != broker_position.symbol:
                raise ValueError("Flatten quote symbol must match broker position symbol.")

            side = (
                OrderSide.SELL
                if float(broker_position.net_quantity) > 0
                else OrderSide.BUY
            )
            intent = OrderIntent(
                intent_id=(
                    "approved-flatten-"
                    f"{broker_position.symbol}-{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}-{index}"
                ),
                strategy_id=strategy_id,
                symbol=broker_position.symbol,
                created_at=timestamp,
                side=side,
                order_type=OrderType.MARKET,
                quantity=abs(float(broker_position.net_quantity)),
                reduce_only=True,
                paper=False,
                metadata={
                    "reason": "position_protection_reconciliation_failed",
                    "source_position_ids": list(broker_position.source_position_ids),
                    "position_id": broker_position.position_id,
                },
            )
            decision = RiskDecision(
                status=RiskDecisionStatus.APPROVED,
                checked_at=quote.received_timestamp,
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                reason="approved_position_protection_flatten",
                projected_position=0.0,
            )
            self._record_risk_decision(decision)
            updates.append(self._submit_risk_approved_order(intent, quote, decision))

        return tuple(updates)

    def _submit_risk_approved_order(
        self,
        intent: OrderIntent,
        quote: ExecutionQuote,
        risk_decision: RiskDecision,
    ) -> ExecutionUpdate:
        oms_record = OmsOrderRecord.new(intent)
        checked_record = transition_order(
            oms_record,
            OmsOrderStatus.CHECKED,
            updated_at=risk_decision.checked_at,
        )
        self._remember_oms_record(checked_record)
        self._record_oms_event(checked_record, "checked")
        self._record_order_request(intent, quote)

        sent_record = transition_order(
            checked_record,
            OmsOrderStatus.SENT,
            updated_at=quote.received_timestamp,
        )
        self._remember_oms_record(sent_record)
        self._record_oms_event(sent_record, "sent")

        update = self.require_execution_router().submit_order(intent, quote)
        final_record = self._transition_oms_after_execution_update(sent_record, update)
        self._remember_oms_record(final_record)
        self._record_oms_event(final_record, "final")
        self._state_tracker.apply_update(update)
        self._remember_account_state(update)
        self._record_order_response(update, causation_id=sent_record.intent.intent_id)
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
        for update in updates:
            self._remember_account_state(update)
            oms_record = self._transition_existing_oms_after_execution_update(update)
            if oms_record is not None:
                self._remember_oms_record(oms_record)
                self._record_oms_event(oms_record, "process_update")
            self._record_order_response(update, causation_id=update.order.intent.intent_id)
        if self.config.monitoring.metrics_enabled and updates:
            self._metrics.increment(
                "scalper_ai_execution_updates_total",
                float(len(updates)),
                requested_mode=self.requested_mode,
                effective_mode=self.effective_mode,
            )
        return updates

    def _reset_recoverable_state(self) -> None:
        self._state_tracker.clear()
        self._oms_records.clear()
        self._last_cash_balance_by_route.clear()
        self._last_equity_by_route.clear()
        self._session_kill_switch_enabled = False
        self._symbol_kill_switches.clear()
        self._recovered_execution_update_count = 0
        self._recovered_oms_record_count = 0

    def _recover_state_from_store(self) -> None:
        if self._state_store is None:
            return

        execution_updates = self._state_store.list_execution_updates()
        self._state_tracker.apply_updates(execution_updates)
        for update in execution_updates:
            self._remember_account_state(update)
        for record in self._state_store.list_oms_records():
            self._remember_oms_record(record, persist=False)
        for state in self._state_store.list_kill_switch_states():
            if not state.enabled:
                continue
            if state.scope is KillSwitchScope.SESSION:
                self._session_kill_switch_enabled = True
            elif state.symbol is not None:
                self._symbol_kill_switches.add(state.symbol)

        self._recovered_execution_update_count = len(execution_updates)
        self._recovered_oms_record_count = len(self._oms_records)

    def _block_unsafe_recovered_startup(self) -> None:
        live_open_orders = tuple(
            order for order in self._state_tracker.list_orders(paper=False) if order.is_open
        )
        if not live_open_orders:
            return
        if self.requested_mode == "live" and self.effective_mode != "live":
            raise RuntimeError(
                "Cannot fall back to paper while durable state contains open live orders."
            )
        if self.effective_mode != "live":
            return

        report_provider = self._resolved_reconciliation_report_provider()
        if report_provider is None:
            raise RuntimeError(
                "Cannot start live runtime with open recovered live orders without reconciliation."
            )
        report = self._last_reconciliation_report
        if report is None:
            try:
                report = report_provider()
            except Exception as exc:
                raise RuntimeError(
                    "Cannot start live runtime because startup reconciliation failed."
                ) from exc
        if report is None:
            raise RuntimeError(
                "Cannot start live runtime because startup reconciliation returned no report."
            )
        self._last_reconciliation_report = report
        if report.has_errors:
            raise RuntimeError(
                "Cannot start live runtime because startup reconciliation found error drift."
            )

    def _run_live_startup_reconciliation(self) -> None:
        if self.effective_mode != "live":
            return
        report_provider = self._resolved_reconciliation_report_provider()
        if report_provider is None:
            raise RuntimeError("No startup reconciliation provider is configured.")
        try:
            report = report_provider()
        except Exception as exc:
            raise RuntimeError("startup reconciliation provider raised an exception.") from exc
        if report is None:
            raise RuntimeError("startup reconciliation returned no report.")
        self._last_reconciliation_report = report
        self._activate_reconciliation_fail_safe(
            report,
            default_reason="startup_reconciliation_error_drift",
        )
        if report.has_errors:
            self._startup_reason = (
                "Startup reconciliation detected error-level broker/internal drift; "
                "session kill-switch is active."
            )

    def _build_risk_context(self, intent: OrderIntent, quote: ExecutionQuote) -> RiskContext:
        if self._risk_context_provider is not None:
            return self._risk_context_provider(intent, quote, self._state_tracker)

        checked_at = quote.received_timestamp
        route_orders = self._state_tracker.list_orders(paper=intent.paper)
        route_positions = {
            position.symbol: position
            for position in self._state_tracker.list_positions(paper=intent.paper)
        }
        return RiskContext(
            checked_at=checked_at,
            positions=route_positions,
            order_timestamps=tuple(order.submitted_at for order in route_orders),
            known_intent_ids=frozenset(order.intent.intent_id for order in route_orders),
            known_broker_order_ids=frozenset(order.broker_order_id for order in route_orders),
            recent_rejection_timestamps=tuple(
                order.updated_at
                for order in route_orders
                if order.status is ExecutionOrderStatus.REJECTED
            ),
            latest_market_data_at=quote.received_timestamp,
            current_spread_pips=(
                _spread_pips_for_quote(quote) if not intent.paper else None
            ),
            realized_pnl_today=sum(
                float(position.realized_pnl) for position in route_positions.values()
            ),
            starting_equity=self._last_equity_by_route.get(intent.paper),
            current_equity=self._last_equity_by_route.get(intent.paper),
            session_kill_switch=self._session_kill_switch_enabled,
            symbol_kill_switches=frozenset(self._symbol_kill_switches),
        )

    def _build_risk_rejected_update(
        self,
        intent: OrderIntent,
        quote: ExecutionQuote,
        context: RiskContext,
        decision: RiskDecision,
    ) -> ExecutionUpdate:
        requested_quantity = self._risk_rejected_quantity(intent, context)
        broker_order_id = f"risk-rejected-{intent.intent_id}"
        rejected_order = ExecutionOrder(
            intent=intent,
            broker_order_id=broker_order_id,
            status=ExecutionOrderStatus.REJECTED,
            submitted_at=decision.checked_at,
            updated_at=decision.checked_at,
            requested_quantity=requested_quantity,
            filled_quantity=0.0,
            remaining_quantity=requested_quantity,
            rejection_reason=decision.reason or "risk_rejected",
        )
        position = self._marked_runtime_position(intent, quote, context)
        cash_balance = self._last_cash_balance_by_route.get(intent.paper, 100_000.0)
        equity = self._last_equity_by_route.get(
            intent.paper,
            calculate_equity(cash_balance, position),
        )
        return ExecutionUpdate(
            order=rejected_order,
            fills=(),
            position=position,
            cash_balance=cash_balance,
            equity=equity,
            quote=quote,
        )

    @staticmethod
    def _risk_rejected_quantity(intent: OrderIntent, context: RiskContext) -> float:
        if intent.quantity is not None:
            return float(intent.quantity)
        current_position = context.positions.get(intent.symbol)
        current_quantity = 0.0 if current_position is None else float(current_position.net_quantity)
        if intent.target_position is not None:
            requested_quantity = abs(float(intent.target_position) - current_quantity)
            if requested_quantity > 0:
                return requested_quantity
        return 1.0

    def _marked_runtime_position(
        self,
        intent: OrderIntent,
        quote: ExecutionQuote,
        context: RiskContext,
    ) -> PositionState:
        return mark_position(
            context.positions.get(intent.symbol),
            symbol=intent.symbol,
            timestamp=quote.received_timestamp,
            mark_price=quote.mid_price,
        )

    def _transition_oms_after_execution_update(
        self,
        record: OmsOrderRecord,
        update: ExecutionUpdate,
    ) -> OmsOrderRecord:
        updated_at = update.order.updated_at
        status = update.order.status
        if status is ExecutionOrderStatus.REJECTED:
            return transition_order(
                record,
                OmsOrderStatus.REJECTED,
                updated_at=updated_at,
                broker_order_id=update.order.broker_order_id,
                rejection_reason=update.order.rejection_reason or "execution_rejected",
            )
        if status is ExecutionOrderStatus.CANCELED:
            return transition_order(
                record,
                OmsOrderStatus.CANCELLED,
                updated_at=updated_at,
                broker_order_id=update.order.broker_order_id,
                cancel_reason=update.order.cancel_reason or "execution_cancelled",
            )

        ack_record = transition_order(
            record,
            OmsOrderStatus.ACK,
            updated_at=updated_at,
            broker_order_id=update.order.broker_order_id,
            filled_quantity=update.order.filled_quantity,
        )
        if status is ExecutionOrderStatus.FILLED:
            return _transition_ack_or_partial_to_filled(ack_record, update)
        if status is ExecutionOrderStatus.PARTIALLY_FILLED:
            return _transition_ack_or_partial_to_partial(ack_record, update)
        return ack_record

    def _transition_existing_oms_after_execution_update(
        self,
        update: ExecutionUpdate,
    ) -> OmsOrderRecord | None:
        record = self._oms_records.get(update.order.intent.intent_id)
        if record is None or record.is_terminal:
            return None

        status = update.order.status
        if status in {ExecutionOrderStatus.ACCEPTED, ExecutionOrderStatus.TRIGGERED}:
            return record
        if status is ExecutionOrderStatus.FILLED:
            return _transition_ack_or_partial_to_filled(record, update)
        if status is ExecutionOrderStatus.PARTIALLY_FILLED:
            return _transition_ack_or_partial_to_partial(record, update)
        if status is ExecutionOrderStatus.CANCELED:
            return transition_order(
                record,
                OmsOrderStatus.CANCELLED,
                updated_at=update.order.updated_at,
                broker_order_id=update.order.broker_order_id,
                cancel_reason=update.order.cancel_reason or "execution_cancelled",
            )
        if status is ExecutionOrderStatus.REJECTED:
            return transition_order(
                record,
                OmsOrderStatus.REJECTED,
                updated_at=update.order.updated_at,
                broker_order_id=update.order.broker_order_id,
                rejection_reason=update.order.rejection_reason or "execution_rejected",
            )
        return record

    def _remember_oms_record(self, record: OmsOrderRecord, *, persist: bool = True) -> None:
        self._oms_records[record.intent.intent_id] = record
        if persist and self._state_store is not None:
            self._state_store.save_oms_record(record)

    def _remember_account_state(self, update: ExecutionUpdate) -> None:
        route = update.order.intent.paper
        self._last_cash_balance_by_route[route] = update.cash_balance
        self._last_equity_by_route[route] = update.equity

    def _record_risk_decision(self, decision: RiskDecision) -> None:
        if self._state_store is not None:
            self._state_store.save_risk_decision(decision)
        self._record_journal_event(
            decision.to_journal_event(
                event_id=f"risk-{decision.intent_id}-{len(self._journal_events) + 1}",
                source="deployment_runtime",
            )
        )

    def _record_oms_event(self, record: OmsOrderRecord, stage: str) -> None:
        self._record_journal_event(
            JournalEvent.from_payload(
                event_id=f"oms-{record.intent.intent_id}-{stage}-{len(self._journal_events) + 1}",
                event_type=JournalEventType.ORDER_RESPONSE,
                payload=_oms_record_to_payload(record, stage=stage),
                recorded_at=record.updated_at,
                source="deployment_runtime",
                event_timestamp=record.updated_at,
                payload_type="OmsOrderRecord",
                correlation_id=record.intent.intent_id,
                strategy_id=record.intent.strategy_id,
                symbol=record.intent.symbol,
            )
        )

    def _record_order_request(self, intent: OrderIntent, quote: ExecutionQuote) -> None:
        self._record_journal_event(
            JournalEvent.from_payload(
                event_id=f"order-request-{intent.intent_id}-{len(self._journal_events) + 1}",
                event_type=JournalEventType.ORDER_REQUEST,
                payload={
                    "intent": intent,
                    "quote": _quote_to_payload(quote),
                },
                recorded_at=quote.received_timestamp,
                source="deployment_runtime",
                event_timestamp=intent.created_at,
                payload_type="RuntimeOrderRequest",
                correlation_id=intent.intent_id,
                strategy_id=intent.strategy_id,
                symbol=intent.symbol,
            )
        )

    def _record_order_response(self, update: ExecutionUpdate, *, causation_id: str) -> None:
        order = update.order
        if self._state_store is not None:
            self._state_store.save_execution_update(update)
        self._record_journal_event(
            JournalEvent.from_payload(
                event_id=f"order-response-{order.intent.intent_id}-{len(self._journal_events) + 1}",
                event_type=JournalEventType.ORDER_RESPONSE,
                payload=_execution_update_to_payload(update),
                recorded_at=order.updated_at,
                source="deployment_runtime",
                event_timestamp=order.updated_at,
                payload_type="ExecutionUpdate",
                correlation_id=order.intent.intent_id,
                causation_id=causation_id,
                strategy_id=order.intent.strategy_id,
                symbol=order.intent.symbol,
            )
        )
        for fill in update.fills:
            self._record_journal_event(
                JournalEvent.from_payload(
                    event_id=f"fill-{fill.fill_id}-{len(self._journal_events) + 1}",
                    event_type=JournalEventType.FILL,
                    payload=fill,
                    recorded_at=fill.received_timestamp,
                    source="deployment_runtime",
                    correlation_id=fill.intent_id,
                    causation_id=order.broker_order_id,
                    strategy_id=order.intent.strategy_id,
                    symbol=fill.symbol,
                )
            )
        self._record_journal_event(
            JournalEvent.from_payload(
                event_id=f"position-{order.intent.intent_id}-{len(self._journal_events) + 1}",
                event_type=JournalEventType.POSITION_SNAPSHOT,
                payload=update.position,
                recorded_at=update.position.timestamp,
                source="deployment_runtime",
                correlation_id=order.intent.intent_id,
                causation_id=order.broker_order_id,
                strategy_id=order.intent.strategy_id,
                symbol=update.position.symbol,
            )
        )

    def _record_journal_event(self, event: JournalEvent) -> None:
        self._journal_events.append(event)
        if self._journal_writer is not None:
            self._journal_writer.write(event)

    def _increment_rejected_metric(self, intent: OrderIntent) -> None:
        if not self.config.monitoring.metrics_enabled:
            return
        self._metrics.increment(
            "scalper_ai_execution_orders_rejected_total",
            requested_mode=self.requested_mode,
            effective_mode=self.effective_mode,
            paper=str(intent.paper).lower(),
            source="risk",
        )

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
                try:
                    self._run_live_startup_reconciliation()
                except Exception as exc:
                    live_failure_reason = f"Live startup reconciliation failed: {exc}"
                    self._close_live_adapter()
                    self._router = None
                    self._live_adapter = None
                else:
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

    def _live_startup_blocker(self) -> str | None:
        if not self.config.broker.live_enabled:
            return "Live runtime requested but broker.live_enabled is false."
        if (
            self.config.deployment.require_live_confirmation
            and self._live_confirmation_token != self.config.deployment.live_confirmation_phrase
        ):
            return "Live runtime confirmation phrase is missing or invalid."
        if (
            not self.config.risk.kill_switch_enabled
            and not self.config.broker.allow_live_without_kill_switch
        ):
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
        missing = [
            name for name, path in directories.items() if not path.exists() or not path.is_dir()
        ]
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
                summary=(
                    "Reconciliation provider returned no broker snapshot for a "
                    "live-capable runtime."
                ),
                details={"configured": True, "report_available": False},
            )

        self._last_reconciliation_report = report
        self._activate_reconciliation_fail_safe(
            report,
            default_reason="reconciliation_error_drift",
        )
        if report.has_errors:
            status = HealthStatus.FAIL
            summary = "Reconciliation detected error-level drift between internal and broker state."
        elif report.warning_count > 0:
            status = HealthStatus.WARN
            summary = (
                "Reconciliation detected warning-level drift between internal and broker state."
            )
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
                    summary=(
                        "No broker connectivity provider is required for the current safe mode."
                    ),
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
            (
                0.0
                if snapshot is None or snapshot.snapshot_age_seconds() is None
                else snapshot.snapshot_age_seconds()
            ),
            **labels,
        )
        self._metrics.set_gauge(
            "scalper_ai_broker_ping_latency_ms",
            0.0 if snapshot is None or snapshot.latency_ms is None else snapshot.latency_ms,
            **labels,
        )

    def _resolved_reconciliation_report_provider(self) -> ReconciliationReportProvider | None:
        if self._reconciliation_report_provider is not None:
            return self._reconciliation_report_provider
        if self._resolved_broker_snapshot_provider() is None:
            return None
        return self._build_snapshot_report

    def _resolved_broker_connectivity_provider(self) -> BrokerConnectivityProvider | None:
        if self._broker_connectivity_provider is not None:
            return self._broker_connectivity_provider
        snapshot_provider = self._as_broker_connectivity_provider(
            self._resolved_broker_snapshot_provider()
        )
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
            require_position_stop_loss=self._require_position_stop_loss(),
            require_position_take_profit=self._require_position_take_profit(),
        )

    def _resolved_broker_snapshot_provider(self) -> BrokerSnapshotProvider | None:
        if self._broker_snapshot_provider is not None:
            return self._broker_snapshot_provider
        return self._as_broker_snapshot_provider(self._live_adapter)

    def _as_broker_connectivity_provider(
        self,
        candidate: object | None,
    ) -> BrokerConnectivityProvider | None:
        if candidate is None:
            return None
        provider_method = getattr(candidate, "describe_broker_connectivity", None)
        if not callable(provider_method):
            return None
        return cast(BrokerConnectivityProvider, candidate)

    def _as_broker_snapshot_provider(
        self,
        candidate: object | None,
    ) -> BrokerSnapshotProvider | None:
        if candidate is None:
            return None
        list_orders_method = getattr(candidate, "list_broker_orders", None)
        list_positions_method = getattr(candidate, "list_broker_positions", None)
        if not callable(list_orders_method) or not callable(list_positions_method):
            return None
        return cast(BrokerSnapshotProvider, candidate)

    def _activate_reconciliation_fail_safe(
        self,
        report: ReconciliationReport,
        *,
        default_reason: str,
    ) -> None:
        if not report.has_errors:
            return
        has_position_protection_drift = any(
            issue.code in _POSITION_PROTECTION_ISSUE_CODES for issue in report.issues
        )
        reason = (
            "position_protection_reconciliation_failed"
            if has_position_protection_drift
            else default_reason
        )
        self._session_kill_switch_enabled = True
        if self._state_store is not None:
            self._state_store.save_kill_switch_state(
                KillSwitchState(
                    scope=KillSwitchScope.SESSION,
                    enabled=True,
                    updated_at=report.checked_at,
                    reason=reason,
                )
            )

    def _validate_position_flatten_approval(self, approval_token: str) -> None:
        if approval_token != self.config.deployment.live_confirmation_phrase:
            raise RuntimeError("Approved flatten workflow requires the live confirmation phrase.")

    def _require_position_stop_loss(self) -> bool:
        return (
            self.effective_mode == "live"
            and self.config.broker.live_adapter == "mt5"
            and self.config.broker.mt5.require_stop_loss
        )

    def _require_position_take_profit(self) -> bool:
        return (
            self.effective_mode == "live"
            and self.config.broker.live_adapter == "mt5"
            and self.config.broker.mt5.require_take_profit
        )

    def _close_live_adapter(self) -> None:
        if self._live_adapter is None:
            return
        close_method = getattr(self._live_adapter, "close", None)
        if callable(close_method):
            close_method()


def _position_protection_issue_symbols(report: ReconciliationReport) -> frozenset[str]:
    symbols: set[str] = set()
    for issue in report.issues:
        if issue.code not in _POSITION_PROTECTION_ISSUE_CODES:
            continue
        symbol = None
        if issue.details is not None:
            raw_symbol = issue.details.get("symbol")
            if isinstance(raw_symbol, str) and raw_symbol.strip():
                symbol = raw_symbol
        if symbol is None and issue.scope == "position":
            symbol = issue.reference_id
        if symbol is not None and symbol.strip():
            symbols.add(symbol)
    return frozenset(symbols)


def _oms_record_to_payload(record: OmsOrderRecord, *, stage: str) -> dict[str, object]:
    return {
        "stage": stage,
        "intent_id": record.intent.intent_id,
        "strategy_id": record.intent.strategy_id,
        "symbol": record.intent.symbol,
        "status": record.status.value,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "broker_order_id": record.broker_order_id,
        "filled_quantity": record.filled_quantity,
        "rejection_reason": record.rejection_reason,
        "cancel_reason": record.cancel_reason,
    }


def _transition_ack_or_partial_to_filled(
    record: OmsOrderRecord,
    update: ExecutionUpdate,
) -> OmsOrderRecord:
    return transition_order(
        record,
        OmsOrderStatus.FILLED,
        updated_at=update.order.updated_at,
        broker_order_id=update.order.broker_order_id,
        filled_quantity=update.order.filled_quantity,
    )


def _transition_ack_or_partial_to_partial(
    record: OmsOrderRecord,
    update: ExecutionUpdate,
) -> OmsOrderRecord:
    return transition_order(
        record,
        OmsOrderStatus.PARTIAL,
        updated_at=update.order.updated_at,
        broker_order_id=update.order.broker_order_id,
        filled_quantity=update.order.filled_quantity,
    )


def _quote_to_payload(quote: ExecutionQuote) -> dict[str, object]:
    return {
        "symbol": quote.symbol,
        "event_timestamp": quote.event_timestamp,
        "received_timestamp": quote.received_timestamp,
        "bid": quote.bid,
        "ask": quote.ask,
        "mid_price": quote.mid_price,
        "spread": quote.spread,
        "venue": quote.venue,
    }


def _spread_pips_for_quote(quote: ExecutionQuote) -> float:
    return quote.spread / _default_pip_size_for_symbol(quote.symbol)


def _default_pip_size_for_symbol(symbol: str) -> float:
    normalized = symbol.upper()
    if normalized.endswith("JPY"):
        return 0.01
    return 0.0001


def _execution_update_to_payload(update: ExecutionUpdate) -> dict[str, object]:
    return {
        "order": _execution_order_to_payload(update.order),
        "fill_count": len(update.fills),
        "fills": list(update.fills),
        "position": update.position,
        "cash_balance": update.cash_balance,
        "equity": update.equity,
        "quote": _quote_to_payload(update.quote),
    }


def _execution_order_to_payload(order: ExecutionOrder) -> dict[str, object]:
    return {
        "intent": order.intent,
        "broker_order_id": order.broker_order_id,
        "status": order.status.value,
        "submitted_at": order.submitted_at,
        "updated_at": order.updated_at,
        "requested_quantity": order.requested_quantity,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
        "triggered_at": order.triggered_at,
        "rejection_reason": order.rejection_reason,
        "cancel_reason": order.cancel_reason,
    }

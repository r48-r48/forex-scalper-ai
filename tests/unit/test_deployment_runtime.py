"""Tests for deployment runtime safety, health, and metrics behavior."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scalper_ai.config import AppConfig, load_app_config
from scalper_ai.deployment import (
    DeploymentRuntime,
    HealthStatus,
    MetricsRegistry,
    RuntimeLifecycleState,
)
from scalper_ai.domain import OrderIntent, OrderSide, OrderType
from scalper_ai.execution import (
    BrokerConnectivitySnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    ExecutionOrderStatus,
    ExecutionQuote,
    LiveExecutionStubAdapter,
    Mt5ExecutionAdapter,
    Mt5ExecutionConfig,
    Mt5OrderRequest,
    Mt5OrderState,
    Mt5PositionState,
    ReconciliationIssue,
    ReconciliationReport,
    ReconciliationSeverity,
)
from scalper_ai.journal import JournalEvent, JournalEventType
from scalper_ai.services import OmsOrderStatus
from scalper_ai.utils import resolve_repo_root


def test_load_app_config_supports_paper_overlay() -> None:
    config = load_app_config(config_name="paper", config_dir=resolve_repo_root() / "configs")

    assert config.environment == "paper"
    assert config.runtime.mode == "paper"
    assert config.runtime.paper_trading_default is True
    assert config.monitoring.service_name == "scalper_ai_paper_runtime"


def test_metrics_registry_renders_prometheus_surface() -> None:
    registry = MetricsRegistry(service_name="scalper_ai_runtime")

    registry.increment("scalper_ai_runtime_start_total", requested_mode="paper")
    registry.set_gauge("scalper_ai_runtime_up", 1.0, effective_mode="paper")
    rendered = registry.render_prometheus()

    assert "# TYPE scalper_ai_runtime_start_total counter" in rendered
    assert 'service="scalper_ai_runtime"' in rendered
    assert 'effective_mode="paper"' in rendered


def test_live_runtime_falls_back_to_paper_when_confirmation_is_missing() -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "mode": "live",
                "paper_trading_default": False,
            },
            "broker": {
                "live_enabled": True,
                "live_adapter": "external",
            },
            "deployment": {
                "fallback_to_paper_on_live_failure": True,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )

    runtime = DeploymentRuntime(config)
    summary = runtime.start()

    assert summary.requested_mode == "live"
    assert summary.effective_mode == "paper"
    assert summary.lifecycle_state is RuntimeLifecycleState.DEGRADED
    assert summary.startup_reason == "Live runtime confirmation phrase is missing or invalid."

    snapshot = runtime.health_snapshot()
    assert snapshot.overall_status is HealthStatus.WARN
    assert any(
        check.name == "execution_mode" and check.status is HealthStatus.WARN
        for check in snapshot.checks
    )


def test_live_runtime_raises_when_fallback_is_disabled() -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "mode": "live",
                "paper_trading_default": False,
            },
            "broker": {
                "live_enabled": True,
                "live_adapter": "external",
            },
            "deployment": {
                "fallback_to_paper_on_live_failure": False,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )

    runtime = DeploymentRuntime(config)

    with pytest.raises(RuntimeError, match="confirmation phrase"):
        runtime.start()


def test_health_snapshot_warns_when_runtime_is_stopped() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "research"}})
    runtime = DeploymentRuntime(config)

    runtime.start()
    runtime.stop()
    snapshot = runtime.health_snapshot()

    assert snapshot.overall_status is HealthStatus.WARN
    assert any(
        check.name == "runtime_state" and check.status is HealthStatus.WARN
        for check in snapshot.checks
    )


def test_health_snapshot_warns_on_reconciliation_drift_and_exports_metrics() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    runtime = DeploymentRuntime(
        config,
        reconciliation_report_provider=lambda: ReconciliationReport(
            checked_at=datetime(2026, 3, 28, 9, 0, tzinfo=UTC),
            issues=(
                ReconciliationIssue(
                    scope="position",
                    reference_id="EURUSD",
                    severity=ReconciliationSeverity.WARN,
                    code="average_entry_mismatch",
                    message="Average entry differs slightly.",
                ),
            ),
        ),
    )

    runtime.start()
    snapshot = runtime.health_snapshot()

    assert snapshot.overall_status is HealthStatus.WARN
    assert any(
        check.name == "execution_reconciliation" and check.status is HealthStatus.WARN
        for check in snapshot.checks
    )

    metrics_text = runtime.metrics_text()
    assert "scalper_ai_reconciliation_warning_count" in metrics_text
    assert 'requested_mode="paper"' in metrics_text


def test_health_snapshot_fails_when_reconciliation_provider_raises() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})

    def broken_provider() -> ReconciliationReport | None:
        raise RuntimeError("broker api timeout")

    runtime = DeploymentRuntime(config, reconciliation_report_provider=broken_provider)
    runtime.start()

    snapshot = runtime.health_snapshot()

    assert snapshot.overall_status is HealthStatus.FAIL
    assert any(
        check.name == "execution_reconciliation" and check.status is HealthStatus.FAIL
        for check in snapshot.checks
    )


def test_health_snapshot_fails_when_broker_connectivity_provider_raises() -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "mode": "live",
                "paper_trading_default": False,
            },
            "broker": {
                "live_enabled": True,
                "live_adapter": "stub",
            },
            "deployment": {
                "fallback_to_paper_on_live_failure": False,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )

    class BrokenConnectivityProvider:
        def describe_broker_connectivity(self) -> BrokerConnectivitySnapshot:
            raise RuntimeError("broker heartbeat timeout")

    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=LiveExecutionStubAdapter,
        broker_connectivity_provider=BrokenConnectivityProvider(),
        reconciliation_report_provider=lambda: ReconciliationReport(
            checked_at=datetime(2026, 3, 28, 9, 0, tzinfo=UTC),
            issues=(),
        ),
        live_confirmation_token="ENABLE_ME",
    )
    runtime.start()

    snapshot = runtime.health_snapshot()

    assert snapshot.overall_status is HealthStatus.FAIL
    assert any(
        check.name == "broker_connectivity" and check.status is HealthStatus.FAIL
        for check in snapshot.checks
    )


def test_health_snapshot_warns_on_stale_broker_snapshot_and_exports_metrics() -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "mode": "live",
                "paper_trading_default": False,
            },
            "broker": {
                "live_enabled": True,
                "live_adapter": "stub",
            },
            "monitoring": {
                "broker_snapshot_stale_after_seconds": 5.0,
            },
            "deployment": {
                "fallback_to_paper_on_live_failure": False,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )
    class StaleConnectivityProvider:
        def describe_broker_connectivity(self) -> BrokerConnectivitySnapshot:
            return BrokerConnectivitySnapshot(
                venue="live_stub",
                checked_at=datetime(2026, 3, 28, 10, 0, 10, tzinfo=UTC),
                connected=True,
                last_snapshot_at=datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC),
                latency_ms=12.5,
            )

    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=LiveExecutionStubAdapter,
        broker_connectivity_provider=StaleConnectivityProvider(),
        reconciliation_report_provider=lambda: ReconciliationReport(
            checked_at=datetime(2026, 3, 28, 10, 0, tzinfo=UTC),
            issues=(),
        ),
        live_confirmation_token="ENABLE_ME",
    )
    runtime.start()

    snapshot = runtime.health_snapshot()

    assert snapshot.overall_status is HealthStatus.WARN
    assert any(
        check.name == "broker_connectivity" and check.status is HealthStatus.WARN
        for check in snapshot.checks
    )

    metrics_text = runtime.metrics_text()
    assert "scalper_ai_broker_snapshot_age_seconds" in metrics_text
    assert "scalper_ai_broker_ping_latency_ms" in metrics_text


def test_runtime_can_build_reconciliation_from_snapshot_provider() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    tracked: dict[str, object] = {}

    class StaticSnapshotProvider:
        def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
            order = tracked["order"]
            return (
                BrokerOrderSnapshot(
                    broker_order_id=order.broker_order_id,
                    symbol=order.intent.symbol,
                    status=order.status,
                    updated_at=order.updated_at,
                    requested_quantity=order.requested_quantity,
                    filled_quantity=order.filled_quantity,
                    remaining_quantity=order.remaining_quantity,
                ),
            )

        def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
            position = tracked["position"]
            return (
                BrokerPositionSnapshot(
                    symbol=position.symbol,
                    timestamp=position.timestamp,
                    net_quantity=position.net_quantity,
                    average_entry_price=position.average_entry_price,
                ),
            )

    runtime = DeploymentRuntime(config, broker_snapshot_provider=StaticSnapshotProvider())
    runtime.start()

    timestamp = datetime(2026, 3, 28, 10, 0, tzinfo=UTC)
    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.0999,
        ask=1.1001,
        venue="paper",
    )
    update = runtime.submit_order(
        OrderIntent(
            intent_id="intent-1",
            strategy_id="paper-strategy",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2.0,
            paper=True,
        ),
        quote,
    )
    tracked["order"] = update.order
    tracked["position"] = update.position

    snapshot = runtime.health_snapshot()

    assert snapshot.overall_status is HealthStatus.PASS
    assert any(
        check.name == "execution_reconciliation" and check.status is HealthStatus.PASS
        for check in snapshot.checks
    )


def test_runtime_risk_rejects_before_router_submit_and_records_oms() -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {"mode": "paper"},
            "risk": {"max_position_size": 1.0},
        }
    )
    adapter = _RejectIfSubmittedAdapter()
    runtime = DeploymentRuntime(config, paper_adapter_factory=lambda: adapter)
    runtime.start()

    timestamp = datetime(2026, 4, 30, 10, 0, tzinfo=UTC)
    update = runtime.submit_order(
        OrderIntent(
            intent_id="risk-blocked-intent",
            strategy_id="risk-runtime-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2.0,
            paper=True,
        ),
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.0999,
            ask=1.1001,
            venue="paper",
        ),
    )

    assert adapter.submit_count == 0
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason == "max_position"
    assert update.position.net_quantity == 0.0
    assert runtime.oms_records[0].status is OmsOrderStatus.REJECTED
    assert runtime.oms_records[0].rejection_reason == "max_position"
    assert any(
        event.event_type is JournalEventType.RISK and event.payload["status"] == "rejected"
        for event in runtime.journal_events
    )
    assert any(
        event.payload_type == "OmsOrderRecord"
        and event.payload["stage"] == "risk_rejected"
        and event.payload["status"] == "rejected"
        for event in runtime.journal_events
    )
    assert "scalper_ai_execution_orders_rejected_total" in runtime.metrics_text()


def test_runtime_success_path_records_risk_oms_and_execution_journal_events() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    writer = _RecordingJournalWriter()
    runtime = DeploymentRuntime(config, journal_writer=writer)
    runtime.start()

    timestamp = datetime(2026, 4, 30, 10, 5, tzinfo=UTC)
    update = runtime.submit_order(
        OrderIntent(
            intent_id="journaled-intent",
            strategy_id="runtime-journal-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2.0,
            paper=True,
        ),
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.0999,
            ask=1.1001,
            venue="paper",
        ),
    )

    assert update.order.status is ExecutionOrderStatus.FILLED
    assert runtime.oms_records[0].status is OmsOrderStatus.FILLED
    assert tuple(writer.events) == runtime.journal_events

    event_types = [event.event_type for event in runtime.journal_events]
    assert JournalEventType.RISK in event_types
    assert JournalEventType.ORDER_REQUEST in event_types
    assert JournalEventType.ORDER_RESPONSE in event_types
    assert JournalEventType.FILL in event_types
    assert JournalEventType.POSITION_SNAPSHOT in event_types
    assert any(
        event.event_type is JournalEventType.RISK and event.payload["status"] == "approved"
        for event in runtime.journal_events
    )
    assert any(
        event.payload_type == "OmsOrderRecord" and event.payload["status"] == "filled"
        for event in runtime.journal_events
    )


def test_runtime_oms_tracks_process_quote_fill_for_open_order() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    runtime = DeploymentRuntime(config)
    runtime.start()

    timestamp = datetime(2026, 4, 30, 10, 10, tzinfo=UTC)
    update = runtime.submit_order(
        OrderIntent(
            intent_id="pending-intent",
            strategy_id="runtime-oms-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=2.0,
            limit_price=1.0995,
            paper=True,
        ),
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.0999,
            ask=1.1001,
            venue="paper",
        ),
    )

    assert update.order.status is ExecutionOrderStatus.ACCEPTED
    assert runtime.oms_records[0].status is OmsOrderStatus.ACK

    fill_timestamp = datetime(2026, 4, 30, 10, 10, 1, tzinfo=UTC)
    updates = runtime.process_quote(
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=fill_timestamp,
            received_timestamp=fill_timestamp,
            bid=1.0992,
            ask=1.0994,
            venue="paper",
        )
    )

    assert len(updates) == 1
    assert updates[0].order.status is ExecutionOrderStatus.FILLED
    assert runtime.oms_records[0].status is OmsOrderStatus.FILLED
    assert any(
        event.payload_type == "OmsOrderRecord"
        and event.payload["stage"] == "process_update"
        and event.payload["status"] == "filled"
        for event in runtime.journal_events
    )


def test_live_runtime_can_use_live_stub_adapter_with_snapshot_reconciliation() -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "mode": "live",
                "paper_trading_default": False,
            },
            "broker": {
                "live_enabled": True,
                "live_adapter": "stub",
            },
            "deployment": {
                "fallback_to_paper_on_live_failure": False,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )
    live_adapter = LiveExecutionStubAdapter()
    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: live_adapter,
        broker_snapshot_provider=live_adapter,
        live_confirmation_token="ENABLE_ME",
    )

    summary = runtime.start()
    assert summary.effective_mode == "live"
    assert summary.lifecycle_state is RuntimeLifecycleState.RUNNING

    timestamp = datetime.now(UTC)
    update = runtime.submit_order(
        OrderIntent(
            intent_id="live-intent",
            strategy_id="live-runtime-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
            paper=False,
        ),
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.0999,
            ask=1.1001,
            venue="broker-feed",
        ),
    )

    assert update.order.intent.paper is False
    assert update.fills[0].venue == "live_stub"

    snapshot = runtime.health_snapshot()
    assert snapshot.overall_status is HealthStatus.PASS
    assert any(
        check.name == "broker_connectivity" and check.status is HealthStatus.PASS
        for check in snapshot.checks
    )
    assert any(
        check.name == "execution_reconciliation" and check.status is HealthStatus.PASS
        for check in snapshot.checks
    )


class _RejectIfSubmittedAdapter:
    def __init__(self) -> None:
        self.submit_count = 0

    def submit_order(self, intent: OrderIntent, quote: ExecutionQuote) -> object:
        self.submit_count += 1
        raise AssertionError("risk rejected orders must not reach the router adapter")

    def process_quote(self, quote: ExecutionQuote) -> tuple[object, ...]:
        return ()

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> object:
        raise KeyError(broker_order_id)

    def get_order(self, broker_order_id: str) -> object | None:
        return None

    def get_position(self, symbol: str, *, quote: ExecutionQuote | None = None) -> object | None:
        return None


class _RecordingJournalWriter:
    def __init__(self) -> None:
        self.events: list[JournalEvent] = []

    def write(self, event: JournalEvent) -> object:
        self.events.append(event)
        return None


def test_live_runtime_can_use_mt5_adapter_skeleton_without_manual_snapshot_provider() -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "mode": "live",
                "paper_trading_default": False,
            },
            "broker": {
                "live_enabled": True,
                "live_adapter": "mt5",
            },
            "deployment": {
                "fallback_to_paper_on_live_failure": False,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )

    class ImmediateFillMt5Client:
        def __init__(self) -> None:
            self._orders: dict[str, Mt5OrderState] = {}
            self._positions: dict[str, Mt5PositionState] = {}

        def submit_order(self, request: Mt5OrderRequest) -> Mt5OrderState:
            state = Mt5OrderState(
                broker_order_id="mt5-order-runtime-1",
                broker_symbol=request.broker_symbol,
                status=ExecutionOrderStatus.FILLED,
                submitted_at=request.submitted_at,
                updated_at=request.submitted_at,
                requested_volume_lots=request.volume_lots,
                filled_volume_lots=request.volume_lots,
                remaining_volume_lots=0.0,
                average_fill_price=1.1001,
            )
            self._orders[state.broker_order_id] = state
            signed_volume = (
                request.volume_lots
                if request.side is OrderSide.BUY
                else -request.volume_lots
            )
            self._positions[request.broker_symbol] = Mt5PositionState(
                broker_symbol=request.broker_symbol,
                timestamp=request.submitted_at,
                net_volume_lots=signed_volume,
                average_entry_price=1.1001,
            )
            return state

        def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> Mt5OrderState:
            raise NotImplementedError

        def get_order(self, broker_order_id: str) -> Mt5OrderState | None:
            return self._orders.get(broker_order_id)

        def list_orders(self) -> tuple[Mt5OrderState, ...]:
            return tuple(self._orders.values())

        def get_position(self, broker_symbol: str) -> Mt5PositionState | None:
            return self._positions.get(broker_symbol)

        def list_positions(self) -> tuple[Mt5PositionState, ...]:
            return tuple(self._positions.values())

        def is_connected(self) -> bool:
            return True

        def ping_latency_ms(self) -> float | None:
            return 5.0

    live_adapter = Mt5ExecutionAdapter(
        ImmediateFillMt5Client(),
        config=Mt5ExecutionConfig(base_units_per_lot=100_000.0),
    )
    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: live_adapter,
        live_confirmation_token="ENABLE_ME",
    )

    summary = runtime.start()
    assert summary.effective_mode == "live"
    assert summary.lifecycle_state is RuntimeLifecycleState.RUNNING

    timestamp = datetime.now(UTC)
    update = runtime.submit_order(
        OrderIntent(
            intent_id="mt5-live-intent",
            strategy_id="mt5-runtime-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100_000.0,
            paper=False,
        ),
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.0999,
            ask=1.1001,
            venue="broker-feed",
        ),
    )

    assert update.order.status is ExecutionOrderStatus.FILLED
    snapshot = runtime.health_snapshot()
    assert snapshot.overall_status is HealthStatus.PASS
    assert any(
        check.name == "broker_connectivity" and check.status is HealthStatus.PASS
        for check in snapshot.checks
    )
    assert any(
        check.name == "execution_reconciliation" and check.status is HealthStatus.PASS
        for check in snapshot.checks
    )

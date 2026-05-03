"""Tests for deployment runtime safety, health, and metrics behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scalper_ai.config import AppConfig, load_app_config
from scalper_ai.deployment import (
    DataFreshnessSnapshot,
    DeploymentRuntime,
    GuardStateSnapshot,
    HealthStatus,
    MetricsRegistry,
    ModelHealthSnapshot,
    RuntimeDataFreshnessProvider,
    RuntimeGuardStateProvider,
    RuntimeLifecycleState,
    RuntimeModelHealthProvider,
)
from scalper_ai.domain import FeatureSnapshot, OrderIntent, OrderSide, OrderType, PositionState
from scalper_ai.execution import (
    BrokerConnectivitySnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    ExecutionOrder,
    ExecutionOrderStatus,
    ExecutionQuote,
    ExecutionUpdate,
    KillSwitchScope,
    KillSwitchState,
    LiveExecutionStubAdapter,
    Mt5ExecutionAdapter,
    Mt5ExecutionConfig,
    Mt5OrderRequest,
    Mt5OrderState,
    Mt5PositionState,
    ReconciliationIssue,
    ReconciliationReport,
    ReconciliationSeverity,
    SqliteExecutionStateStore,
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


def test_live_health_snapshot_warns_when_dependency_providers_are_missing() -> None:
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
    runtime.start()

    snapshot = runtime.health_snapshot()

    assert snapshot.overall_status is HealthStatus.WARN
    missing_checks = {
        check.name: check
        for check in snapshot.checks
        if check.name in {"data_freshness", "model_readiness", "dependency_guards"}
    }
    assert set(missing_checks) == {
        "data_freshness",
        "model_readiness",
        "dependency_guards",
    }
    assert all(check.status is HealthStatus.WARN for check in missing_checks.values())


def test_health_snapshot_warns_on_stale_data_freshness_provider_and_exports_metrics() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    checked_at = datetime(2026, 3, 28, 10, 0, 10, tzinfo=UTC)
    provider = _StaticDataFreshnessProvider(
        DataFreshnessSnapshot(
            checked_at=checked_at,
            latest_market_data_at=datetime(2026, 3, 28, 10, 0, tzinfo=UTC),
            latest_features_at=datetime(2026, 3, 28, 10, 0, 2, tzinfo=UTC),
            market_data_stale_after_seconds=5.0,
            features_stale_after_seconds=5.0,
            source="unit-test",
        )
    )
    runtime = DeploymentRuntime(config, data_freshness_provider=provider)
    runtime.start()

    snapshot = runtime.health_snapshot()

    check = next(check for check in snapshot.checks if check.name == "data_freshness")
    assert snapshot.overall_status is HealthStatus.WARN
    assert check.status is HealthStatus.WARN
    assert check.details["market_data_age_seconds"] == 10.0
    assert check.details["features_age_seconds"] == 8.0

    metrics_text = runtime.metrics_text()
    assert "scalper_ai_market_data_age_seconds" in metrics_text
    assert "scalper_ai_feature_age_seconds" in metrics_text


def test_live_health_snapshot_fails_when_model_provider_is_not_ready() -> None:
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
    dependency_provider = _HealthyRuntimeDependencyProvider()
    timestamp = datetime(2026, 3, 28, 10, 2, tzinfo=UTC)
    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: live_adapter,
        broker_snapshot_provider=live_adapter,
        data_freshness_provider=dependency_provider,
        model_health_provider=_StaticModelHealthProvider(
            ModelHealthSnapshot(
                checked_at=timestamp,
                ready=False,
                model_id="eurusd-transformer",
                reason="artifact_missing",
                source="unit-test",
            )
        ),
        guard_state_provider=dependency_provider,
        live_confirmation_token="ENABLE_ME",
    )
    runtime.start()

    snapshot = runtime.health_snapshot()

    check = next(check for check in snapshot.checks if check.name == "model_readiness")
    assert snapshot.overall_status is HealthStatus.FAIL
    assert check.status is HealthStatus.FAIL
    assert check.details["reason"] == "artifact_missing"


def test_health_snapshot_warns_when_dependency_guards_are_active() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    runtime = DeploymentRuntime(
        config,
        guard_state_provider=_StaticGuardStateProvider(
            GuardStateSnapshot(
                checked_at=datetime(2026, 3, 28, 10, 3, tzinfo=UTC),
                volatility_guard_active=True,
                news_guard_active=True,
                volatility_reason="spread_regime",
                news_reason="central_bank_event",
                source="unit-test",
            )
        ),
    )
    runtime.start()

    snapshot = runtime.health_snapshot()

    check = next(check for check in snapshot.checks if check.name == "dependency_guards")
    assert snapshot.overall_status is HealthStatus.WARN
    assert check.status is HealthStatus.WARN
    assert check.details["volatility_reason"] == "spread_regime"
    assert check.details["news_reason"] == "central_bank_event"


def test_runtime_risk_rejects_when_model_health_provider_is_unhealthy() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    adapter = _RejectIfSubmittedAdapter()
    timestamp = datetime(2026, 3, 28, 10, 4, tzinfo=UTC)
    runtime = DeploymentRuntime(
        config,
        paper_adapter_factory=lambda: adapter,
        model_health_provider=_StaticModelHealthProvider(
            ModelHealthSnapshot(
                checked_at=timestamp,
                ready=False,
                reason="no_recent_prediction",
                source="unit-test",
            )
        ),
    )
    runtime.start()

    update = runtime.submit_order(
        OrderIntent(
            intent_id="blocked-by-model-health",
            strategy_id="dependency-health-test",
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
    assert update.order.rejection_reason == "model_unhealthy"


def test_runtime_risk_rejects_when_data_freshness_provider_is_stale() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    adapter = _RejectIfSubmittedAdapter()
    timestamp = datetime(2026, 3, 28, 10, 4, 30, tzinfo=UTC)
    runtime = DeploymentRuntime(
        config,
        paper_adapter_factory=lambda: adapter,
        data_freshness_provider=_StaticDataFreshnessProvider(
            DataFreshnessSnapshot(
                checked_at=timestamp,
                latest_market_data_at=timestamp - timedelta(seconds=5),
                latest_features_at=timestamp,
                market_data_stale_after_seconds=2.0,
                features_stale_after_seconds=30.0,
                source="unit-test",
            )
        ),
    )
    runtime.start()

    update = runtime.submit_order(
        OrderIntent(
            intent_id="blocked-by-stale-data-provider",
            strategy_id="dependency-health-test",
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
    assert update.order.rejection_reason == "stale_market_data"


def test_runtime_uses_concrete_dependency_providers_for_health_and_risk() -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    adapter = _RejectIfSubmittedAdapter()
    timestamp = datetime(2026, 5, 3, 10, 6, tzinfo=UTC)
    data_provider = RuntimeDataFreshnessProvider(
        market_data_stale_after_seconds=30.0,
        features_stale_after_seconds=30.0,
        clock=lambda: timestamp,
    )
    model_provider = RuntimeModelHealthProvider(
        model_id="runtime-test-model",
        prediction_stale_after_seconds=30.0,
        clock=lambda: timestamp,
    )
    guard_provider = RuntimeGuardStateProvider(
        volatility_threshold=0.002,
        clock=lambda: timestamp,
    )
    feature_snapshot = FeatureSnapshot(
        symbol="EURUSD",
        event_timestamp=timestamp,
        available_timestamp=timestamp,
        feature_set="runtime-test",
        feature_version="1",
        values={"realized_volatility": 0.003, "spread": 0.0002},
    )
    data_provider.record_market_data_timestamp(timestamp, symbol="EURUSD")
    data_provider.record_feature_snapshot(feature_snapshot)
    model_provider.mark_loaded(timestamp=timestamp)
    model_provider.record_prediction(timestamp=timestamp)
    guard_provider.record_feature_snapshot(feature_snapshot)
    runtime = DeploymentRuntime(
        config,
        paper_adapter_factory=lambda: adapter,
        data_freshness_provider=data_provider,
        model_health_provider=model_provider,
        guard_state_provider=guard_provider,
    )
    runtime.start()

    snapshot = runtime.health_snapshot()
    dependency_check = next(
        check for check in snapshot.checks if check.name == "dependency_guards"
    )
    assert dependency_check.status is HealthStatus.WARN

    update = runtime.submit_order(
        OrderIntent(
            intent_id="blocked-by-concrete-volatility-provider",
            strategy_id="dependency-health-test",
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
    assert update.order.rejection_reason == "volatility_guard"


def test_health_snapshot_reports_recovered_session_kill_switch(tmp_path) -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")
    store.save_kill_switch_state(
        KillSwitchState(
            scope=KillSwitchScope.SESSION,
            enabled=True,
            updated_at=datetime(2026, 3, 28, 10, 5, tzinfo=UTC),
            reason="operator_pause",
        )
    )
    runtime = DeploymentRuntime(config, state_store=store)
    runtime.start()

    snapshot = runtime.health_snapshot()

    check = next(check for check in snapshot.checks if check.name == "risk_guardrails")
    assert snapshot.overall_status is HealthStatus.FAIL
    assert check.status is HealthStatus.FAIL
    assert check.details["session_kill_switch"] is True
    assert "scalper_ai_session_kill_switch_active" in runtime.metrics_text()


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


def test_live_runtime_rejects_wide_spread_before_router_submit() -> None:
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
            "risk": {"max_spread_pips": 1.0},
        }
    )
    adapter = _RejectIfSubmittedAdapter()
    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: adapter,
        reconciliation_report_provider=lambda: ReconciliationReport(
            checked_at=datetime(2026, 4, 30, 10, 2, tzinfo=UTC),
            issues=(),
        ),
        live_confirmation_token="ENABLE_ME",
    )
    runtime.start()

    timestamp = datetime(2026, 4, 30, 10, 2, tzinfo=UTC)
    update = runtime.submit_order(
        OrderIntent(
            intent_id="wide-spread-live-intent",
            strategy_id="risk-runtime-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2.0,
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

    assert adapter.submit_count == 0
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason == "max_spread"


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


def test_runtime_recovers_durable_state_and_blocks_duplicate_intent(tmp_path) -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")
    timestamp = datetime(2026, 4, 30, 10, 20, tzinfo=UTC)
    intent = OrderIntent(
        intent_id="recovered-intent",
        strategy_id="runtime-recovery-test",
        symbol="EURUSD",
        created_at=timestamp,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=2.0,
        paper=True,
    )
    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.0999,
        ask=1.1001,
        venue="paper",
    )

    first_runtime = DeploymentRuntime(config, state_store=store)
    first_runtime.start()
    first_update = first_runtime.submit_order(intent, quote)
    first_runtime.stop()

    assert first_update.order.status is ExecutionOrderStatus.FILLED
    assert store.count_rows("execution_updates") == 1

    adapter = _RejectIfSubmittedAdapter()
    recovered_runtime = DeploymentRuntime(
        config,
        paper_adapter_factory=lambda: adapter,
        state_store=store,
    )
    recovered_runtime.start()

    assert recovered_runtime.oms_records[0].status is OmsOrderStatus.FILLED
    assert "scalper_ai_runtime_recovered_execution_updates" in recovered_runtime.metrics_text()

    retry_timestamp = datetime(2026, 4, 30, 10, 20, 1, tzinfo=UTC)
    retry_update = recovered_runtime.submit_order(
        intent,
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=retry_timestamp,
            received_timestamp=retry_timestamp,
            bid=1.0998,
            ask=1.1002,
            venue="paper",
        ),
    )

    assert adapter.submit_count == 0
    assert retry_update.order.status is ExecutionOrderStatus.REJECTED
    assert retry_update.order.rejection_reason == "duplicate_intent"


def test_runtime_recovers_durable_kill_switch_before_router_submit(tmp_path) -> None:
    config = AppConfig.model_validate({"runtime": {"mode": "paper"}})
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")
    timestamp = datetime(2026, 4, 30, 10, 23, tzinfo=UTC)
    store.save_kill_switch_state(
        KillSwitchState(
            scope=KillSwitchScope.SESSION,
            enabled=True,
            updated_at=timestamp,
            reason="operator_pause",
        )
    )
    adapter = _RejectIfSubmittedAdapter()
    runtime = DeploymentRuntime(
        config,
        paper_adapter_factory=lambda: adapter,
        state_store=store,
    )
    runtime.start()

    update = runtime.submit_order(
        OrderIntent(
            intent_id="blocked-by-recovered-kill-switch",
            strategy_id="runtime-recovery-test",
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
    assert update.order.rejection_reason == "session_kill_switch"


def test_live_runtime_fail_safe_blocks_after_unprotected_position_reconciliation(
    tmp_path,
) -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "mode": "live",
                "paper_trading_default": False,
            },
            "broker": {
                "live_enabled": True,
                "live_adapter": "mt5",
                "mt5": {"require_stop_loss": True},
            },
            "deployment": {
                "fallback_to_paper_on_live_failure": False,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )
    timestamp = datetime(2026, 4, 30, 10, 45, tzinfo=UTC)
    adapter = _RejectIfSubmittedAdapter()
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")

    class UnprotectedPositionProvider:
        def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
            return ()

        def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
            return (
                BrokerPositionSnapshot(
                    symbol="EURUSD",
                    timestamp=timestamp,
                    net_quantity=100_000.0,
                    average_entry_price=1.1000,
                ),
            )

    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: adapter,
        broker_snapshot_provider=UnprotectedPositionProvider(),
        live_confirmation_token="ENABLE_ME",
        state_store=store,
    )
    runtime.start()

    snapshot = runtime.health_snapshot()

    assert snapshot.overall_status is HealthStatus.FAIL
    reconciliation_check = next(
        check for check in snapshot.checks if check.name == "execution_reconciliation"
    )
    assert reconciliation_check.status is HealthStatus.FAIL
    assert "position_stop_loss_missing" in reconciliation_check.details["issue_codes"]
    assert any(
        state.scope is KillSwitchScope.SESSION
        and state.enabled
        and state.reason == "position_protection_reconciliation_failed"
        for state in store.list_kill_switch_states()
    )

    update = runtime.submit_order(
        OrderIntent(
            intent_id="blocked-after-unprotected-position",
            strategy_id="runtime-protection-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100_000.0,
            stop_loss_price=1.0950,
            paper=False,
        ),
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.1000,
            ask=1.1001,
            venue="broker-feed",
        ),
    )

    assert adapter.submit_count == 0
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason == "session_kill_switch"


def test_live_runtime_approved_flatten_closes_unprotected_position_after_fail_safe(
    tmp_path,
) -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "mode": "live",
                "paper_trading_default": False,
            },
            "broker": {
                "live_enabled": True,
                "live_adapter": "mt5",
                "mt5": {"require_stop_loss": True},
            },
            "deployment": {
                "fallback_to_paper_on_live_failure": False,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )
    timestamp = datetime(2026, 4, 30, 11, 15, tzinfo=UTC)
    adapter = _RecordingFlattenAdapter()
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")

    class UnprotectedPositionProvider:
        def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
            return ()

        def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
            return (
                BrokerPositionSnapshot(
                    symbol="EURUSD",
                    timestamp=timestamp,
                    net_quantity=100_000.0,
                    average_entry_price=1.1000,
                    position_id="position-1",
                    source_position_ids=("position-1",),
                ),
            )

    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: adapter,
        broker_snapshot_provider=UnprotectedPositionProvider(),
        live_confirmation_token="ENABLE_ME",
        state_store=store,
    )
    runtime.start()
    runtime.health_snapshot()

    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.1000,
        ask=1.1001,
        venue="broker-feed",
    )
    updates = runtime.flatten_unprotected_positions(
        {"EURUSD": quote},
        approval_token="ENABLE_ME",
        created_at=timestamp,
    )

    assert len(updates) == 1
    assert adapter.submit_count == 1
    submitted_intent = adapter.submitted_intents[0]
    assert submitted_intent.side is OrderSide.SELL
    assert submitted_intent.reduce_only is True
    assert submitted_intent.paper is False
    assert submitted_intent.quantity == pytest.approx(100_000.0)
    assert submitted_intent.metadata["reason"] == "position_protection_reconciliation_failed"
    assert updates[0].order.status is ExecutionOrderStatus.FILLED
    assert updates[0].position.net_quantity == pytest.approx(0.0)
    assert any(
        record.intent.intent_id == submitted_intent.intent_id
        and record.status is OmsOrderStatus.FILLED
        for record in runtime.oms_records
    )
    assert any(
        event.event_type is JournalEventType.RISK
        and event.payload["reason"] == "approved_position_protection_flatten"
        for event in runtime.journal_events
    )


def test_live_runtime_approved_repair_updates_position_protection_from_reconciliation(
    tmp_path,
) -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "mode": "live",
                "paper_trading_default": False,
            },
            "broker": {
                "live_enabled": True,
                "live_adapter": "mt5",
                "mt5": {"require_stop_loss": True},
            },
            "deployment": {
                "fallback_to_paper_on_live_failure": False,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )
    timestamp = datetime(2026, 4, 30, 11, 45, tzinfo=UTC)
    report = ReconciliationReport(
        checked_at=timestamp,
        issues=(
            ReconciliationIssue(
                scope="position",
                reference_id="position-1",
                severity=ReconciliationSeverity.ERROR,
                code="position_stop_loss_missing",
                message="Broker position is missing expected stop-loss protection.",
                details={
                    "symbol": "EURUSD",
                    "field_name": "stop_loss_price",
                    "expected_value": 1.0950,
                    "source_order_ids": ["order-1"],
                },
            ),
        ),
    )
    adapter = _RecordingProtectionRepairAdapter()
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")

    class RepairablePositionProvider:
        def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
            return ()

        def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
            return (
                BrokerPositionSnapshot(
                    symbol="EURUSD",
                    timestamp=timestamp,
                    net_quantity=100_000.0,
                    average_entry_price=1.1000,
                    position_id="position-1",
                    source_position_ids=("position-1",),
                ),
            )

    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: adapter,
        broker_snapshot_provider=RepairablePositionProvider(),
        reconciliation_report_provider=lambda: report,
        live_confirmation_token="ENABLE_ME",
        state_store=store,
    )
    runtime.start()
    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.1000,
        ask=1.1001,
        venue="broker-feed",
    )

    repaired = runtime.repair_unprotected_positions(
        {"EURUSD": quote},
        approval_token="ENABLE_ME",
        created_at=timestamp,
    )

    assert len(repaired) == 1
    assert adapter.repair_calls == [
        {
            "symbol": "EURUSD",
            "position_id": "position-1",
            "stop_loss_price": 1.0950,
            "take_profit_price": None,
        }
    ]
    assert repaired[0].stop_loss_price == pytest.approx(1.0950)
    assert any(
        event.payload_type == "PositionProtectionRepair"
        and event.payload["reason"] == "approved_position_protection_repair"
        for event in runtime.journal_events
    )
    assert "scalper_ai_position_protection_repairs_total" in runtime.metrics_text()


def test_live_runtime_resets_session_kill_switch_after_clean_reconciliation(
    tmp_path,
) -> None:
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
    timestamp = datetime(2026, 4, 30, 12, 15, tzinfo=UTC)
    clean_report = ReconciliationReport(checked_at=timestamp, issues=())
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")
    store.save_kill_switch_state(
        KillSwitchState(
            scope=KillSwitchScope.SESSION,
            enabled=True,
            updated_at=timestamp,
            reason="position_protection_reconciliation_failed",
        )
    )
    adapter = _RecordingFlattenAdapter()
    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: adapter,
        reconciliation_report_provider=lambda: clean_report,
        live_confirmation_token="ENABLE_ME",
        state_store=store,
    )
    runtime.start()

    report = runtime.reset_session_kill_switch_after_reconciliation(
        approval_token="ENABLE_ME",
        reset_at=timestamp,
    )
    update = runtime.submit_order(
        OrderIntent(
            intent_id="allowed-after-clean-reset",
            strategy_id="runtime-reset-test",
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
            bid=1.1000,
            ask=1.1001,
            venue="broker-feed",
        ),
    )

    assert report is clean_report
    assert update.order.status is ExecutionOrderStatus.FILLED
    assert adapter.submit_count == 1
    assert any(
        state.scope is KillSwitchScope.SESSION
        and not state.enabled
        and state.reason == "clean_reconciliation_reset"
        for state in store.list_kill_switch_states()
    )
    assert any(
        event.payload_type == "KillSwitchReset"
        and event.payload["reason"] == "clean_reconciliation_reset"
        for event in runtime.journal_events
    )
    assert "scalper_ai_session_kill_switch_resets_total" in runtime.metrics_text()


def test_live_runtime_refuses_kill_switch_reset_when_reconciliation_has_errors(
    tmp_path,
) -> None:
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
    timestamp = datetime(2026, 4, 30, 12, 20, tzinfo=UTC)
    clean_report = ReconciliationReport(checked_at=timestamp, issues=())
    dirty_report = ReconciliationReport(
        checked_at=timestamp,
        issues=(
            ReconciliationIssue(
                scope="position",
                reference_id="EURUSD",
                severity=ReconciliationSeverity.ERROR,
                code="broker_only_position",
                message="Broker has exposure that is absent from runtime state.",
            ),
        ),
    )
    reports = [clean_report, dirty_report]
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")
    store.save_kill_switch_state(
        KillSwitchState(
            scope=KillSwitchScope.SESSION,
            enabled=True,
            updated_at=timestamp,
            reason="startup_reconciliation_error_drift",
        )
    )
    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=LiveExecutionStubAdapter,
        reconciliation_report_provider=lambda: reports.pop(0),
        live_confirmation_token="ENABLE_ME",
        state_store=store,
    )
    runtime.start()

    with pytest.raises(RuntimeError, match="error drift"):
        runtime.reset_session_kill_switch_after_reconciliation(
            approval_token="ENABLE_ME",
            reset_at=timestamp,
        )

    assert any(
        state.scope is KillSwitchScope.SESSION
        and state.enabled
        and state.reason == "post_repair_reconciliation_error_drift"
        for state in store.list_kill_switch_states()
    )


def test_live_runtime_approved_flatten_requires_confirmation_phrase(tmp_path) -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "mode": "live",
                "paper_trading_default": False,
            },
            "broker": {
                "live_enabled": True,
                "live_adapter": "mt5",
                "mt5": {"require_stop_loss": True},
            },
            "deployment": {
                "fallback_to_paper_on_live_failure": False,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )
    adapter = _RecordingFlattenAdapter()
    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: adapter,
        broker_snapshot_provider=_EmptyBrokerSnapshotProvider(),
        live_confirmation_token="ENABLE_ME",
        state_store=SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite"),
    )
    runtime.start()

    with pytest.raises(RuntimeError, match="confirmation phrase"):
        runtime.flatten_unprotected_positions({}, approval_token="wrong")

    assert adapter.submit_count == 0


def test_live_startup_blocks_paper_fallback_when_recovered_live_order_is_open(tmp_path) -> None:
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")
    timestamp = datetime(2026, 4, 30, 10, 25, tzinfo=UTC)
    intent = OrderIntent(
        intent_id="open-live-intent",
        strategy_id="runtime-recovery-test",
        symbol="EURUSD",
        created_at=timestamp,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1.0,
        limit_price=1.0995,
        paper=False,
    )
    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.0999,
        ask=1.1001,
        venue="broker-feed",
    )
    store.save_execution_update(
        ExecutionUpdate(
            order=ExecutionOrder(
                intent=intent,
                broker_order_id="live-open-1",
                status=ExecutionOrderStatus.ACCEPTED,
                submitted_at=timestamp,
                updated_at=timestamp,
                requested_quantity=1.0,
                filled_quantity=0.0,
                remaining_quantity=1.0,
            ),
            fills=(),
            position=PositionState(
                symbol="EURUSD",
                timestamp=timestamp,
                net_quantity=0.0,
                average_entry_price=0.0,
                mark_price=quote.mid_price,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                exposure_quote=0.0,
            ),
            cash_balance=100_000.0,
            equity=100_000.0,
            quote=quote,
        )
    )
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
                "fallback_to_paper_on_live_failure": True,
                "require_live_confirmation": True,
                "live_confirmation_phrase": "ENABLE_ME",
            },
        }
    )
    runtime = DeploymentRuntime(config, state_store=store)

    with pytest.raises(RuntimeError, match="Cannot fall back to paper"):
        runtime.start()


def test_live_startup_reconciliation_kill_switches_broker_only_position(tmp_path) -> None:
    timestamp = datetime(2026, 4, 30, 11, 40, tzinfo=UTC)
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
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")
    adapter = _RejectIfSubmittedAdapter()

    class BrokerOnlyPositionProvider:
        def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
            return ()

        def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
            return (
                BrokerPositionSnapshot(
                    symbol="EURUSD",
                    timestamp=timestamp,
                    net_quantity=100_000.0,
                    average_entry_price=1.1000,
                    position_id="broker-position-1",
                    source_position_ids=("broker-position-1",),
                ),
            )

    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: adapter,
        broker_snapshot_provider=BrokerOnlyPositionProvider(),
        live_confirmation_token="ENABLE_ME",
        state_store=store,
    )

    summary = runtime.start()

    assert summary.effective_mode == "live"
    assert summary.lifecycle_state is RuntimeLifecycleState.RUNNING
    assert summary.startup_reason is not None
    assert "session kill-switch is active" in summary.startup_reason
    assert any(
        state.scope is KillSwitchScope.SESSION
        and state.enabled
        and state.reason == "startup_reconciliation_error_drift"
        for state in store.list_kill_switch_states()
    )

    update = runtime.submit_order(
        OrderIntent(
            intent_id="blocked-after-broker-only-startup",
            strategy_id="runtime-recovery-test",
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
            bid=1.1000,
            ask=1.1001,
            venue="broker-feed",
        ),
    )

    assert adapter.submit_count == 0
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason == "session_kill_switch"


def test_live_startup_reconciliation_provider_failure_blocks_start() -> None:
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

    def broken_report_provider() -> ReconciliationReport | None:
        raise RuntimeError("broker history unavailable")

    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=LiveExecutionStubAdapter,
        reconciliation_report_provider=broken_report_provider,
        live_confirmation_token="ENABLE_ME",
    )

    with pytest.raises(RuntimeError, match="Live startup reconciliation failed"):
        runtime.start()


def test_live_startup_reconciliation_missing_report_blocks_start() -> None:
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
    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=LiveExecutionStubAdapter,
        reconciliation_report_provider=lambda: None,
        live_confirmation_token="ENABLE_ME",
    )

    with pytest.raises(RuntimeError, match="startup reconciliation returned no report"):
        runtime.start()


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
    dependency_provider = _HealthyRuntimeDependencyProvider()
    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: live_adapter,
        broker_snapshot_provider=live_adapter,
        data_freshness_provider=dependency_provider,
        model_health_provider=dependency_provider,
        guard_state_provider=dependency_provider,
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
            bid=1.1000,
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


class _EmptyBrokerSnapshotProvider:
    def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
        return ()

    def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
        return ()


class _StaticDataFreshnessProvider:
    def __init__(self, snapshot: DataFreshnessSnapshot) -> None:
        self._snapshot = snapshot

    def describe_data_freshness(self) -> DataFreshnessSnapshot:
        return self._snapshot


class _StaticModelHealthProvider:
    def __init__(self, snapshot: ModelHealthSnapshot) -> None:
        self._snapshot = snapshot

    def describe_model_health(self) -> ModelHealthSnapshot:
        return self._snapshot


class _StaticGuardStateProvider:
    def __init__(self, snapshot: GuardStateSnapshot) -> None:
        self._snapshot = snapshot

    def describe_guard_state(self) -> GuardStateSnapshot:
        return self._snapshot


class _HealthyRuntimeDependencyProvider:
    def describe_data_freshness(self) -> DataFreshnessSnapshot:
        timestamp = datetime.now(UTC)
        return DataFreshnessSnapshot(
            checked_at=timestamp,
            latest_market_data_at=timestamp,
            latest_features_at=timestamp,
            market_data_stale_after_seconds=30.0,
            features_stale_after_seconds=30.0,
            source="unit-test",
        )

    def describe_model_health(self) -> ModelHealthSnapshot:
        timestamp = datetime.now(UTC)
        return ModelHealthSnapshot(
            checked_at=timestamp,
            ready=True,
            model_id="healthy-test-model",
            last_loaded_at=timestamp,
            last_prediction_at=timestamp,
            last_prediction_stale_after_seconds=30.0,
            source="unit-test",
        )

    def describe_guard_state(self) -> GuardStateSnapshot:
        return GuardStateSnapshot(
            checked_at=datetime.now(UTC),
            volatility_guard_active=False,
            news_guard_active=False,
            source="unit-test",
        )


class _RecordingFlattenAdapter:
    def __init__(self) -> None:
        self.submit_count = 0
        self.submitted_intents: list[OrderIntent] = []
        self._orders: dict[str, ExecutionOrder] = {}

    def submit_order(self, intent: OrderIntent, quote: ExecutionQuote) -> ExecutionUpdate:
        self.submit_count += 1
        self.submitted_intents.append(intent)
        broker_order_id = f"flatten-order-{self.submit_count}"
        order = ExecutionOrder(
            intent=intent,
            broker_order_id=broker_order_id,
            status=ExecutionOrderStatus.FILLED,
            submitted_at=quote.received_timestamp,
            updated_at=quote.received_timestamp,
            requested_quantity=float(intent.quantity or 1.0),
            filled_quantity=float(intent.quantity or 1.0),
            remaining_quantity=0.0,
        )
        self._orders[broker_order_id] = order
        position = PositionState(
            symbol=intent.symbol,
            timestamp=quote.received_timestamp,
            net_quantity=0.0,
            average_entry_price=0.0,
            mark_price=quote.mid_price,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            exposure_quote=0.0,
        )
        return ExecutionUpdate(
            order=order,
            fills=(),
            position=position,
            cash_balance=100_000.0,
            equity=100_000.0,
            quote=quote,
        )

    def process_quote(self, quote: ExecutionQuote) -> tuple[ExecutionUpdate, ...]:
        return ()

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> ExecutionUpdate:
        raise KeyError(broker_order_id)

    def get_order(self, broker_order_id: str) -> ExecutionOrder | None:
        return self._orders.get(broker_order_id)

    def get_position(
        self,
        symbol: str,
        *,
        quote: ExecutionQuote | None = None,
    ) -> PositionState | None:
        return None


class _RecordingProtectionRepairAdapter(_RecordingFlattenAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.repair_calls: list[dict[str, object]] = []

    def repair_position_protection(
        self,
        symbol: str,
        *,
        position_id: str,
        stop_loss_price: float | None,
        take_profit_price: float | None,
        quote: ExecutionQuote,
        timestamp: datetime | None = None,
    ) -> BrokerPositionSnapshot:
        self.repair_calls.append(
            {
                "symbol": symbol,
                "position_id": position_id,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
            }
        )
        return BrokerPositionSnapshot(
            symbol=symbol,
            timestamp=timestamp or quote.received_timestamp,
            net_quantity=100_000.0,
            average_entry_price=1.1000,
            position_id=position_id,
            source_position_ids=(position_id,),
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        )


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
    dependency_provider = _HealthyRuntimeDependencyProvider()
    runtime = DeploymentRuntime(
        config,
        live_adapter_factory=lambda: live_adapter,
        data_freshness_provider=dependency_provider,
        model_health_provider=dependency_provider,
        guard_state_provider=dependency_provider,
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
            bid=1.1000,
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

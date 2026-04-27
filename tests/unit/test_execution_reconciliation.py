"""Tests for broker/internal execution reconciliation helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from scalper_ai.domain import OrderIntent, OrderSide, OrderType, PositionMode, PositionState
from scalper_ai.execution import (
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    ExecutionQuote,
    ExecutionOrder,
    ExecutionOrderStatus,
    ExecutionStateTracker,
    PaperExecutionAdapter,
    ReconciliationSeverity,
    build_snapshot_reconciliation_report,
    build_reconciliation_report,
    reconcile_order,
    reconcile_position,
)


def test_reconcile_order_flags_missing_open_broker_order() -> None:
    order = _make_order(status=ExecutionOrderStatus.ACCEPTED, filled_quantity=0.0, remaining_quantity=2.0)

    issues = reconcile_order(order, None)

    assert len(issues) == 1
    assert issues[0].code == "missing_broker_order"
    assert issues[0].severity is ReconciliationSeverity.ERROR


def test_reconcile_order_flags_quantity_mismatch() -> None:
    order = _make_order(status=ExecutionOrderStatus.PARTIALLY_FILLED, filled_quantity=1.0, remaining_quantity=1.0)
    broker = BrokerOrderSnapshot(
        broker_order_id=order.broker_order_id,
        symbol=order.intent.symbol,
        status=ExecutionOrderStatus.PARTIALLY_FILLED,
        updated_at=order.updated_at,
        requested_quantity=2.0,
        filled_quantity=1.5,
        remaining_quantity=0.5,
    )

    issues = reconcile_order(order, broker)

    assert {issue.code for issue in issues} == {"filled_quantity_mismatch", "remaining_quantity_mismatch"}
    assert all(issue.severity is ReconciliationSeverity.ERROR for issue in issues)


def test_reconcile_position_flags_quantity_and_entry_mismatches() -> None:
    internal_position = PositionState(
        symbol="EURUSD",
        timestamp=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
        net_quantity=2.0,
        average_entry_price=1.1000,
        mark_price=1.1002,
        realized_pnl=0.0,
        unrealized_pnl=0.0004,
        exposure_quote=2.2004,
        position_mode=PositionMode.NETTING,
    )
    broker_position = BrokerPositionSnapshot(
        symbol="EURUSD",
        timestamp=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
        net_quantity=1.0,
        average_entry_price=1.1010,
    )

    issues = reconcile_position(internal_position, broker_position)

    assert {issue.code for issue in issues} == {"position_quantity_mismatch", "average_entry_mismatch"}
    assert any(issue.severity is ReconciliationSeverity.ERROR for issue in issues)
    assert any(issue.severity is ReconciliationSeverity.WARN for issue in issues)


def test_build_reconciliation_report_collects_unknown_broker_orders() -> None:
    order = _make_order(status=ExecutionOrderStatus.FILLED, filled_quantity=2.0, remaining_quantity=0.0)
    broker_orders = {
        "broker-extra": BrokerOrderSnapshot(
            broker_order_id="broker-extra",
            symbol="EURUSD",
            status=ExecutionOrderStatus.ACCEPTED,
            updated_at=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
            requested_quantity=1.0,
            filled_quantity=0.0,
            remaining_quantity=1.0,
        )
    }

    report = build_reconciliation_report(
        internal_orders=[order],
        broker_orders=broker_orders,
        internal_position=None,
        broker_position=None,
    )

    assert report.warning_count == 1
    assert report.error_count == 0
    assert report.issues[0].code == "unknown_broker_order"


def test_build_snapshot_reconciliation_report_uses_tracked_internal_state() -> None:
    timestamp = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
    adapter = PaperExecutionAdapter()
    state_tracker = ExecutionStateTracker()
    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.0999,
        ask=1.1001,
        venue="paper",
    )
    update = adapter.submit_order(
        OrderIntent(
            intent_id="intent-tracked",
            strategy_id="strategy-1",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2.0,
            paper=True,
        ),
        quote,
    )
    state_tracker.apply_update(update)

    class StaticSnapshotProvider:
        def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
            return (
                BrokerOrderSnapshot(
                    broker_order_id=update.order.broker_order_id,
                    symbol="EURUSD",
                    status=update.order.status,
                    updated_at=update.order.updated_at,
                    requested_quantity=update.order.requested_quantity,
                    filled_quantity=update.order.filled_quantity,
                    remaining_quantity=update.order.remaining_quantity,
                ),
            )

        def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
            return (
                BrokerPositionSnapshot(
                    symbol="EURUSD",
                    timestamp=timestamp,
                    net_quantity=2.0,
                    average_entry_price=update.position.average_entry_price,
                ),
            )

    report = build_snapshot_reconciliation_report(
        state_tracker=state_tracker,
        snapshot_provider=StaticSnapshotProvider(),
        paper=True,
    )

    assert report.error_count == 0
    assert report.warning_count == 0


def _make_order(
    *,
    status: ExecutionOrderStatus,
    filled_quantity: float,
    remaining_quantity: float,
) -> ExecutionOrder:
    created_at = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
    intent = OrderIntent(
        intent_id="intent-1",
        strategy_id="strategy-1",
        symbol="EURUSD",
        created_at=created_at,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=2.0,
        limit_price=1.1000,
        paper=False,
    )
    return ExecutionOrder(
        intent=intent,
        broker_order_id="broker-order-1",
        status=status,
        submitted_at=created_at,
        updated_at=created_at,
        requested_quantity=2.0,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining_quantity,
    )

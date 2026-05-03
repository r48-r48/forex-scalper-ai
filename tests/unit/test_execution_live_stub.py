"""Tests for the concrete live execution stub adapter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scalper_ai.domain import OrderIntent, OrderSide, OrderType
from scalper_ai.execution import (
    ExecutionOrderStatus,
    ExecutionQuote,
    LiveExecutionStubAdapter,
    LiveExecutionStubConfig,
)


def test_live_stub_accepts_live_orders_and_exports_broker_snapshots() -> None:
    adapter = LiveExecutionStubAdapter(
        config=LiveExecutionStubConfig(
            initial_cash=1_000.0,
            slippage_bps=5.0,
            commission_bps=10.0,
            default_venue="live_stub",
        )
    )
    timestamp = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.0999,
        ask=1.1001,
        venue="broker-feed",
    )

    update = adapter.submit_order(
        OrderIntent(
            intent_id="live-order",
            strategy_id="live-stub-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2.0,
            paper=False,
        ),
        quote,
    )

    assert update.order.status is ExecutionOrderStatus.FILLED
    assert update.order.intent.paper is False
    assert update.fills[0].venue == "live_stub"

    broker_orders = adapter.list_broker_orders()
    broker_positions = adapter.list_broker_positions()
    connectivity = adapter.describe_broker_connectivity()
    assert len(broker_orders) == 1
    assert broker_orders[0].status is ExecutionOrderStatus.FILLED
    assert len(broker_positions) == 1
    assert broker_positions[0].net_quantity == pytest.approx(2.0)
    assert connectivity.connected is True
    assert connectivity.venue == "live_stub"
    assert connectivity.last_snapshot_at == timestamp
    assert connectivity.snapshot_age_seconds() is not None
    assert adapter.get_order(update.order.broker_order_id).intent.paper is False


def test_live_stub_rejects_paper_orders() -> None:
    adapter = LiveExecutionStubAdapter()
    timestamp = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="paper=False"):
        adapter.submit_order(
            OrderIntent(
                intent_id="wrong-order",
                strategy_id="live-stub-test",
                symbol="EURUSD",
                created_at=timestamp,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=1.0,
                paper=True,
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

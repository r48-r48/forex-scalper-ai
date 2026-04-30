"""Unit tests for order, fill, and position state models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from scalper_ai.domain import (
    FillEvent,
    LiquidityFlag,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionState,
)


def test_order_intent_requires_exactly_one_execution_objective() -> None:
    with pytest.raises(ValidationError, match="Exactly one of quantity or target_position"):
        OrderIntent(
            intent_id="intent-1",
            strategy_id="scalper",
            symbol="EURUSD",
            created_at=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10000.0,
            target_position=10000.0,
        )


def test_order_intent_validates_limit_order_shape() -> None:
    with pytest.raises(ValidationError, match="require limit_price"):
        OrderIntent(
            intent_id="intent-2",
            strategy_id="scalper",
            symbol="EURUSD",
            created_at=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10000.0,
        )


def test_order_intent_validates_stop_order_shape() -> None:
    with pytest.raises(ValidationError, match="require stop_price"):
        OrderIntent(
            intent_id="intent-3",
            strategy_id="scalper",
            symbol="EURUSD",
            created_at=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            side=OrderSide.SELL,
            order_type=OrderType.STOP,
            quantity=10000.0,
        )


def test_order_intent_validates_pending_order_protective_prices() -> None:
    with pytest.raises(ValidationError, match="Buy stop_loss_price"):
        OrderIntent(
            intent_id="intent-protection-1",
            strategy_id="scalper",
            symbol="EURUSD",
            created_at=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10000.0,
            limit_price=1.1000,
            stop_loss_price=1.1001,
        )

    with pytest.raises(ValidationError, match="Sell take_profit_price"):
        OrderIntent(
            intent_id="intent-protection-2",
            strategy_id="scalper",
            symbol="EURUSD",
            created_at=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=10000.0,
            limit_price=1.1000,
            take_profit_price=1.1001,
        )


def test_fill_event_rejects_negative_costs() -> None:
    with pytest.raises(ValidationError):
        FillEvent(
            fill_id="fill-1",
            intent_id="intent-1",
            symbol="EURUSD",
            event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            received_timestamp=datetime(2026, 3, 26, 9, 0, 1, tzinfo=UTC),
            side=OrderSide.BUY,
            fill_price=1.0813,
            fill_quantity=10000.0,
            commission=-0.1,
        )


def test_fill_event_json_round_trip_preserves_schema() -> None:
    fill = FillEvent(
        fill_id="fill-2",
        intent_id="intent-2",
        broker_order_id="broker-17",
        symbol="EURUSD",
        event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
        received_timestamp=datetime(2026, 3, 26, 9, 0, 1, tzinfo=UTC),
        side=OrderSide.SELL,
        fill_price=1.08125,
        fill_quantity=15000.0,
        commission=0.3,
        spread_cost=0.4,
        slippage_cost=0.1,
        liquidity_flag=LiquidityFlag.TAKER,
        venue="MT5",
    )

    restored = FillEvent.from_json_bytes(fill.to_json_bytes())

    assert restored == fill
    assert '"fill_id":"fill-2"' in fill.to_json_str()
    assert '"event_timestamp":"2026-03-26T09:00:00Z"' in fill.to_json_str()


def test_position_state_allows_zero_entry_price_when_flat() -> None:
    state = PositionState(
        symbol="EURUSD",
        timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
        net_quantity=0.0,
        average_entry_price=0.0,
        mark_price=1.0812,
        realized_pnl=1.5,
        unrealized_pnl=0.0,
        exposure_quote=0.0,
    )

    assert state.average_entry_price == 0.0


def test_position_state_requires_positive_entry_for_open_position() -> None:
    with pytest.raises(ValidationError, match="positive average_entry_price"):
        PositionState(
            symbol="EURUSD",
            timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            net_quantity=10000.0,
            average_entry_price=0.0,
            mark_price=1.0812,
            realized_pnl=0.0,
            unrealized_pnl=2.0,
            exposure_quote=10812.0,
        )

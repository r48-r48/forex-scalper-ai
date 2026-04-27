"""Unit tests for OMS lifecycle helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scalper_ai.domain import OrderIntent, OrderSide, OrderType
from scalper_ai.services import (
    OmsOrderRecord,
    OmsOrderStatus,
    build_emergency_flatten_intent,
    transition_order,
)


BASE_TS = datetime(2026, 4, 27, 11, 0, tzinfo=timezone.utc)


def test_oms_order_lifecycle_accepts_valid_transitions() -> None:
    record = OmsOrderRecord.new(_intent())

    record = transition_order(
        record,
        OmsOrderStatus.CHECKED,
        updated_at=BASE_TS + timedelta(seconds=1),
    )
    record = transition_order(
        record,
        OmsOrderStatus.SENT,
        updated_at=BASE_TS + timedelta(seconds=2),
    )
    record = transition_order(
        record,
        OmsOrderStatus.ACK,
        updated_at=BASE_TS + timedelta(seconds=3),
        broker_order_id="broker-1",
    )
    record = transition_order(
        record,
        OmsOrderStatus.PARTIAL,
        updated_at=BASE_TS + timedelta(seconds=4),
        filled_quantity=5_000.0,
    )
    record = transition_order(
        record,
        OmsOrderStatus.FILLED,
        updated_at=BASE_TS + timedelta(seconds=5),
        filled_quantity=10_000.0,
    )
    record = transition_order(
        record,
        OmsOrderStatus.RECONCILED,
        updated_at=BASE_TS + timedelta(seconds=6),
    )

    assert record.status is OmsOrderStatus.RECONCILED
    assert record.broker_order_id == "broker-1"
    assert record.filled_quantity == 10_000.0
    assert record.is_terminal is True


def test_oms_order_lifecycle_rejects_invalid_transition() -> None:
    record = OmsOrderRecord.new(_intent())

    with pytest.raises(ValueError, match="Invalid OMS transition"):
        transition_order(record, OmsOrderStatus.FILLED, updated_at=BASE_TS + timedelta(seconds=1))


def test_oms_order_lifecycle_requires_terminal_reasons() -> None:
    record = transition_order(
        OmsOrderRecord.new(_intent()),
        OmsOrderStatus.CHECKED,
        updated_at=BASE_TS + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="rejection_reason"):
        transition_order(record, OmsOrderStatus.REJECTED, updated_at=BASE_TS + timedelta(seconds=2))

    rejected = transition_order(
        record,
        OmsOrderStatus.REJECTED,
        updated_at=BASE_TS + timedelta(seconds=2),
        rejection_reason="risk rejected",
    )

    assert rejected.rejection_reason == "risk rejected"


def test_emergency_flatten_intent_sells_long_position_and_buys_short_position() -> None:
    sell_intent = build_emergency_flatten_intent(
        intent_id="flatten-long",
        strategy_id="risk",
        symbol="EURUSD",
        current_net_quantity=25_000.0,
        created_at=BASE_TS,
        paper=True,
    )
    buy_intent = build_emergency_flatten_intent(
        intent_id="flatten-short",
        strategy_id="risk",
        symbol="EURUSD",
        current_net_quantity=-10_000.0,
        created_at=BASE_TS,
        paper=True,
    )

    assert sell_intent is not None
    assert sell_intent.side is OrderSide.SELL
    assert sell_intent.quantity == 25_000.0
    assert sell_intent.order_type is OrderType.MARKET
    assert sell_intent.reduce_only is True
    assert sell_intent.metadata == {"reason": "emergency_flatten"}
    assert buy_intent is not None
    assert buy_intent.side is OrderSide.BUY
    assert buy_intent.quantity == 10_000.0


def test_emergency_flatten_intent_returns_none_when_flat() -> None:
    intent = build_emergency_flatten_intent(
        intent_id="flatten-flat",
        strategy_id="risk",
        symbol="EURUSD",
        current_net_quantity=0.0,
        created_at=BASE_TS,
    )

    assert intent is None


def _intent() -> OrderIntent:
    return OrderIntent(
        intent_id="intent-1",
        strategy_id="strategy-1",
        symbol="EURUSD",
        created_at=BASE_TS,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10_000.0,
        paper=True,
    )

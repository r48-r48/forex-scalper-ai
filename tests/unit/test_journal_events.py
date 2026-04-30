"""Unit tests for unified journal event contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scalper_ai.domain import (
    EventSource,
    FillEvent,
    LiquidityFlag,
    OrderIntent,
    OrderSide,
    OrderType,
    TickEvent,
)
from scalper_ai.journal import JournalEvent, JournalEventType, journal_events_to_flat_records


def test_journal_event_from_domain_payload_infers_core_metadata() -> None:
    tick = TickEvent(
        symbol="EURUSD",
        venue="MT5",
        event_timestamp=datetime(2026, 4, 27, 10, 0, tzinfo=UTC),
        received_timestamp=datetime(2026, 4, 27, 10, 0, 0, 50_000, tzinfo=UTC),
        bid=1.1010,
        ask=1.1012,
        source=EventSource.REPLAY,
    )

    event = JournalEvent.from_payload(
        event_id="journal-tick-1",
        event_type=JournalEventType.MARKET_DATA,
        payload=tick,
        recorded_at=datetime(2026, 4, 27, 10, 0, 1, tzinfo=UTC),
        source="replay",
        correlation_id="decision-1",
    )

    assert event.event_type is JournalEventType.MARKET_DATA
    assert event.event_timestamp == tick.event_timestamp
    assert event.symbol == "EURUSD"
    assert event.payload_type == "TickEvent"
    assert event.payload["event_timestamp"] == "2026-04-27T10:00:00Z"
    assert event.payload["source"] == "replay"
    assert event.correlation_id == "decision-1"


def test_journal_event_from_mapping_payload_normalizes_nested_values() -> None:
    recorded_at = datetime(2026, 4, 27, 10, 1, tzinfo=UTC)
    payload = {
        "signal_id": "signal-1",
        "symbol": "EURUSD",
        "created_at": recorded_at,
        "side": OrderSide.BUY,
        "features": {
            "spread": 0.0002,
            "source": EventSource.REPLAY,
        },
    }

    event = JournalEvent.from_payload(
        event_id="journal-signal-1",
        event_type="signal_event",
        payload=payload,
        recorded_at=recorded_at,
        source="strategy",
        strategy_id="micro-v1",
    )

    assert event.event_type is JournalEventType.SIGNAL
    assert event.event_timestamp == recorded_at
    assert event.symbol == "EURUSD"
    assert event.payload["created_at"] == "2026-04-27T10:01:00Z"
    assert event.payload["side"] == "buy"
    assert event.payload["features"]["source"] == "replay"


def test_journal_flat_records_are_parquet_friendly() -> None:
    intent = OrderIntent(
        intent_id="intent-1",
        strategy_id="micro-v1",
        symbol="EURUSD",
        created_at=datetime(2026, 4, 27, 10, 2, tzinfo=UTC),
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10_000.0,
        paper=True,
    )
    event = JournalEvent.from_payload(
        event_id="journal-order-request-1",
        event_type=JournalEventType.ORDER_REQUEST,
        payload=intent,
        recorded_at=datetime(2026, 4, 27, 10, 2, 1, tzinfo=UTC),
        source="oms",
        correlation_id="decision-1",
        strategy_id=intent.strategy_id,
    )

    flat_records = journal_events_to_flat_records((event,))

    assert flat_records == [event.to_flat_record()]
    flat = flat_records[0]
    assert flat["event_type"] == "order_request_event"
    assert flat["event_timestamp"] == "2026-04-27T10:02:00Z"
    assert flat["symbol"] == "EURUSD"
    assert '"intent_id":"intent-1"' in flat["payload_json"]


def test_journal_fill_payload_preserves_broker_deal_attribution() -> None:
    timestamp = datetime(2026, 4, 27, 10, 2, tzinfo=UTC)
    fill = FillEvent(
        fill_id="mt5-deal-5001",
        intent_id="intent-1",
        broker_order_id="order-1",
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        side=OrderSide.BUY,
        fill_price=1.1002,
        fill_quantity=100_000.0,
        commission=2.5,
        spread_cost=10.0,
        slippage_cost=0.0,
        broker_deal_id="5001",
        broker_symbol="EURUSD",
        broker_position_id="7001",
        broker_commission=-2.0,
        broker_fee=-0.5,
        broker_swap=0.25,
        liquidity_flag=LiquidityFlag.UNKNOWN,
        venue="MT5",
    )

    event = JournalEvent.from_payload(
        event_id="journal-fill-1",
        event_type=JournalEventType.FILL,
        payload=fill,
        recorded_at=timestamp,
        source="deployment_runtime",
    )

    assert event.payload["broker_deal_id"] == "5001"
    assert event.payload["broker_position_id"] == "7001"
    assert event.payload["broker_commission"] == -2.0
    assert event.payload["broker_fee"] == -0.5
    assert event.payload["broker_swap"] == 0.25


def test_journal_event_requires_utc_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        JournalEvent.from_payload(
            event_id="journal-risk-1",
            event_type=JournalEventType.RISK,
            payload={"risk_check": "max_position", "accepted": False},
            recorded_at=datetime(2026, 4, 27, 10, 3),
            source="risk",
        )


def test_all_required_journal_event_types_are_present() -> None:
    assert {event_type.value for event_type in JournalEventType} == {
        "market_data_event",
        "signal_event",
        "order_request_event",
        "order_response_event",
        "fill_event",
        "position_snapshot",
        "risk_event",
        "latency_event",
    }

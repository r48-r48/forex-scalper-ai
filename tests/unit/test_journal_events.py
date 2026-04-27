"""Unit tests for unified journal event contracts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scalper_ai.domain import EventSource, OrderIntent, OrderSide, OrderType, TickEvent
from scalper_ai.journal import JournalEvent, JournalEventType, journal_events_to_flat_records


def test_journal_event_from_domain_payload_infers_core_metadata() -> None:
    tick = TickEvent(
        symbol="EURUSD",
        venue="MT5",
        event_timestamp=datetime(2026, 4, 27, 10, 0, tzinfo=timezone.utc),
        received_timestamp=datetime(2026, 4, 27, 10, 0, 0, 50_000, tzinfo=timezone.utc),
        bid=1.1010,
        ask=1.1012,
        source=EventSource.REPLAY,
    )

    event = JournalEvent.from_payload(
        event_id="journal-tick-1",
        event_type=JournalEventType.MARKET_DATA,
        payload=tick,
        recorded_at=datetime(2026, 4, 27, 10, 0, 1, tzinfo=timezone.utc),
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
    recorded_at = datetime(2026, 4, 27, 10, 1, tzinfo=timezone.utc)
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
        created_at=datetime(2026, 4, 27, 10, 2, tzinfo=timezone.utc),
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10_000.0,
        paper=True,
    )
    event = JournalEvent.from_payload(
        event_id="journal-order-request-1",
        event_type=JournalEventType.ORDER_REQUEST,
        payload=intent,
        recorded_at=datetime(2026, 4, 27, 10, 2, 1, tzinfo=timezone.utc),
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

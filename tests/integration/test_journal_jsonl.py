"""Integration tests for JSONL journal write/read round trips."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from scalper_ai.domain import FillEvent, LiquidityFlag, OrderSide, PositionMode, PositionState
from scalper_ai.journal import (
    JournalEvent,
    JournalEventType,
    JsonlJournalWriter,
    read_jsonl_journal,
)


def test_jsonl_journal_writer_round_trips_execution_events(tmp_path: Path) -> None:
    fill = FillEvent(
        fill_id="fill-1",
        intent_id="intent-1",
        broker_order_id="broker-1",
        symbol="EURUSD",
        event_timestamp=datetime(2026, 4, 27, 10, 4, tzinfo=UTC),
        received_timestamp=datetime(2026, 4, 27, 10, 4, 0, 20_000, tzinfo=UTC),
        side=OrderSide.BUY,
        fill_price=1.1002,
        fill_quantity=10_000.0,
        commission=0.4,
        spread_cost=0.2,
        slippage_cost=0.1,
        liquidity_flag=LiquidityFlag.TAKER,
        venue="MT5",
    )
    position = PositionState(
        symbol="EURUSD",
        timestamp=datetime(2026, 4, 27, 10, 4, 1, tzinfo=UTC),
        net_quantity=10_000.0,
        average_entry_price=1.1002,
        mark_price=1.1003,
        realized_pnl=0.0,
        unrealized_pnl=1.0,
        exposure_quote=11_003.0,
        last_fill_id=fill.fill_id,
        position_mode=PositionMode.NETTING,
    )
    latency_payload = {
        "operation": "order_send",
        "symbol": "EURUSD",
        "latency_ms": 7.5,
        "timestamp": datetime(2026, 4, 27, 10, 4, 2, tzinfo=UTC),
    }
    recorded_at = datetime(2026, 4, 27, 10, 4, 3, tzinfo=UTC)
    events = (
        JournalEvent.from_payload(
            event_id="journal-fill-1",
            event_type=JournalEventType.FILL,
            payload=fill,
            recorded_at=recorded_at,
            source="execution",
            correlation_id="decision-1",
            causation_id="order-response-1",
        ),
        JournalEvent.from_payload(
            event_id="journal-position-1",
            event_type=JournalEventType.POSITION_SNAPSHOT,
            payload=position,
            recorded_at=recorded_at,
            source="portfolio",
            correlation_id="decision-1",
            causation_id="journal-fill-1",
        ),
        JournalEvent.from_payload(
            event_id="journal-latency-1",
            event_type=JournalEventType.LATENCY,
            payload=latency_payload,
            recorded_at=recorded_at,
            source="runtime",
            correlation_id="decision-1",
        ),
    )
    output_path = tmp_path / "audit" / "events.jsonl"

    writer = JsonlJournalWriter(output_path, append=False)
    assert writer.write_batch(events) == output_path

    restored = read_jsonl_journal(output_path)

    assert restored == events
    assert restored[0].payload["commission"] == 0.4
    assert restored[1].payload["position_mode"] == "netting"
    assert restored[2].payload["timestamp"] == "2026-04-27T10:04:02Z"

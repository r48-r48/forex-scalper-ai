"""Integration-style tests for end-to-end domain event round-trips."""

from __future__ import annotations

from datetime import UTC, datetime

from scalper_ai.domain import (
    BookLevel,
    BookSide,
    BookSnapshot,
    EventSource,
    FeatureSnapshot,
    FillEvent,
    LiquidityFlag,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionMode,
    PositionState,
    TickEvent,
)


def test_domain_event_chain_round_trip_serialization() -> None:
    tick = TickEvent(
        symbol="EURUSD",
        venue="MT5",
        event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
        received_timestamp=datetime(2026, 3, 26, 9, 0, 0, 100000, tzinfo=UTC),
        bid=1.0812,
        ask=1.0813,
        bid_size=2.0,
        ask_size=1.8,
        source=EventSource.REPLAY,
    )
    book = BookSnapshot(
        symbol="EURUSD",
        venue="MT5",
        event_timestamp=tick.event_timestamp,
        received_timestamp=tick.received_timestamp,
        sequence=42,
        bids=[
            BookLevel(side=BookSide.BID, level=1, price=1.0812, size=2.0),
            BookLevel(side=BookSide.BID, level=2, price=1.0811, size=3.0),
        ],
        asks=[
            BookLevel(side=BookSide.ASK, level=1, price=1.0813, size=1.8),
            BookLevel(side=BookSide.ASK, level=2, price=1.0814, size=2.4),
        ],
        checksum="book-42",
        is_full_snapshot=False,
    )
    features = FeatureSnapshot(
        symbol="EURUSD",
        event_timestamp=tick.event_timestamp,
        available_timestamp=datetime(2026, 3, 26, 9, 0, 1, tzinfo=UTC),
        feature_set="microstructure",
        feature_version="v1",
        values={
            "spread": 0.0001,
            "mid_return_1s": 0.00002,
            "quote_intensity": 5.0,
        },
        source=EventSource.REPLAY,
        tags={"session": "london"},
    )
    intent = OrderIntent(
        intent_id="intent-42",
        strategy_id="transformer_policy",
        symbol="EURUSD",
        created_at=features.available_timestamp,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=25000.0,
        limit_price=1.08125,
        paper=True,
        metadata={"signal_id": "sig-9", "regime": "mean_reversion"},
    )
    fill = FillEvent(
        fill_id="fill-42",
        intent_id=intent.intent_id,
        broker_order_id="broker-42",
        symbol="EURUSD",
        event_timestamp=datetime(2026, 3, 26, 9, 0, 2, tzinfo=UTC),
        received_timestamp=datetime(2026, 3, 26, 9, 0, 2, 50000, tzinfo=UTC),
        side=OrderSide.BUY,
        fill_price=1.08125,
        fill_quantity=25000.0,
        commission=0.5,
        spread_cost=0.3,
        slippage_cost=0.1,
        liquidity_flag=LiquidityFlag.MAKER,
        venue="MT5",
    )
    position = PositionState(
        symbol="EURUSD",
        timestamp=fill.event_timestamp,
        net_quantity=25000.0,
        average_entry_price=1.08125,
        mark_price=1.0813,
        realized_pnl=0.0,
        unrealized_pnl=1.25,
        exposure_quote=27032.5,
        last_fill_id=fill.fill_id,
        position_mode=PositionMode.NETTING,
    )

    for event in (tick, book, features, intent, fill, position):
        payload = event.to_record()
        restored = type(event).from_record(payload)
        assert restored == event
        assert payload == restored.to_record()

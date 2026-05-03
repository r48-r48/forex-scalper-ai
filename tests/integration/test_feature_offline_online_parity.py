"""Integration tests for offline and online feature pipeline parity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scalper_ai.domain import BookLevel, BookSide, BookSnapshot, TickEvent
from scalper_ai.features.offline import (
    build_feature_frame,
    build_feature_snapshots,
    merge_feature_events,
)
from scalper_ai.features.online import OnlineFeatureCalculator
from scalper_ai.features.schema import FeatureConfig


def test_offline_builder_matches_manual_online_replay() -> None:
    ticks = [
        _make_tick(
            offset_seconds=0,
            receive_delay_ms=0,
            bid=1.1000,
            ask=1.1002,
            bid_size=2.0,
            ask_size=3.0,
            last_price=1.1001,
            last_size=1.0,
        ),
        _make_tick(
            offset_seconds=1,
            receive_delay_ms=100,
            bid=1.1001,
            ask=1.1003,
            bid_size=2.5,
            ask_size=2.0,
            last_price=1.1002,
            last_size=1.5,
        ),
        _make_tick(
            offset_seconds=2,
            receive_delay_ms=50,
            bid=1.1000,
            ask=1.1002,
            bid_size=3.0,
            ask_size=2.5,
            last_price=1.1001,
            last_size=2.0,
        ),
    ]
    books = [
        _make_book(
            offset_seconds=0,
            receive_delay_ms=50,
            bid_prices=[1.1000, 1.0999],
            ask_prices=[1.1002, 1.1003],
            bid_sizes=[2.0, 1.0],
            ask_sizes=[3.0, 1.5],
        ),
        _make_book(
            offset_seconds=2,
            receive_delay_ms=10,
            bid_prices=[1.1000, 1.0998],
            ask_prices=[1.1002, 1.1004],
            bid_sizes=[3.0, 1.5],
            ask_sizes=[2.5, 1.0],
        ),
    ]
    config = FeatureConfig(
        volatility_window=3,
        quote_intensity_window_seconds=5.0,
        ofi_window=3,
        toxicity_window=3,
        mlofi_depth=2,
    )

    offline_snapshots = build_feature_snapshots(ticks=ticks, books=books, config=config)

    calculator = OnlineFeatureCalculator(config=config)
    manual_snapshots = [
        calculator.update(event) for event in merge_feature_events(ticks=ticks, books=books)
    ]

    assert [snapshot.to_record() for snapshot in offline_snapshots] == [
        snapshot.to_record() for snapshot in manual_snapshots
    ]


def test_build_feature_frame_exposes_flat_feature_columns() -> None:
    ticks = [
        _make_tick(
            offset_seconds=0,
            receive_delay_ms=0,
            bid=1.1000,
            ask=1.1002,
            bid_size=2.0,
            ask_size=3.0,
            last_price=1.1001,
            last_size=1.0,
        )
    ]
    frame = build_feature_frame(
        ticks=ticks,
        config=FeatureConfig(
            volatility_window=2,
            quote_intensity_window_seconds=10.0,
            ofi_window=2,
            toxicity_window=2,
            mlofi_depth=2,
        ),
    )

    assert frame.shape[0] == 1
    assert "spread" in frame.columns
    assert "mlofi_l1" in frame.columns
    assert "toxicity_vpin" in frame.columns


def _make_tick(
    *,
    offset_seconds: int,
    receive_delay_ms: int,
    bid: float,
    ask: float,
    bid_size: float,
    ask_size: float,
    last_price: float,
    last_size: float,
) -> TickEvent:
    event_timestamp = datetime(2026, 3, 26, 9, 0, 0, tzinfo=UTC) + timedelta(
        seconds=offset_seconds
    )
    received_timestamp = event_timestamp + timedelta(milliseconds=receive_delay_ms)
    return TickEvent(
        symbol="EURUSD",
        venue="TEST",
        event_timestamp=event_timestamp,
        received_timestamp=received_timestamp,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        last_price=last_price,
        last_size=last_size,
    )


def _make_book(
    *,
    offset_seconds: int,
    receive_delay_ms: int,
    bid_prices: list[float],
    ask_prices: list[float],
    bid_sizes: list[float],
    ask_sizes: list[float],
) -> BookSnapshot:
    event_timestamp = datetime(2026, 3, 26, 9, 0, 0, tzinfo=UTC) + timedelta(
        seconds=offset_seconds
    )
    received_timestamp = event_timestamp + timedelta(milliseconds=receive_delay_ms)
    assert len(bid_prices) == len(bid_sizes)
    assert len(ask_prices) == len(ask_sizes)
    bids = [
        BookLevel(side=BookSide.BID, level=index + 1, price=price, size=size)
        for index, (price, size) in enumerate(zip(bid_prices, bid_sizes, strict=True))
    ]
    asks = [
        BookLevel(side=BookSide.ASK, level=index + 1, price=price, size=size)
        for index, (price, size) in enumerate(zip(ask_prices, ask_sizes, strict=True))
    ]
    return BookSnapshot(
        symbol="EURUSD",
        venue="TEST",
        event_timestamp=event_timestamp,
        received_timestamp=received_timestamp,
        bids=bids,
        asks=asks,
    )

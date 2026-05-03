"""Unit tests for primitive microstructure feature helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scalper_ai.domain import BookLevel, BookSide, BookSnapshot, TickEvent
from scalper_ai.features.order_flow import multi_level_ofi, top_of_book_ofi
from scalper_ai.features.primitives import quote_intensity, spread, spread_bps
from scalper_ai.features.toxicity import toxicity_vpin_proxy


def test_spread_and_spread_bps_from_tick() -> None:
    tick = _make_tick(
        offset_seconds=0,
        bid=1.1000,
        ask=1.1002,
        bid_size=2.0,
        ask_size=3.0,
    )

    assert spread(tick) == pytest.approx(0.0002)
    assert spread_bps(tick) == pytest.approx(1.8180165439506397)


def test_quote_intensity_counts_trailing_updates_per_second() -> None:
    timestamps = [
        datetime(2026, 3, 26, 9, 0, 0, tzinfo=UTC),
        datetime(2026, 3, 26, 9, 0, 10, tzinfo=UTC),
        datetime(2026, 3, 26, 9, 0, 20, tzinfo=UTC),
    ]

    value = quote_intensity(
        timestamps,
        now=datetime(2026, 3, 26, 9, 0, 20, tzinfo=UTC),
        window_seconds=20.0,
    )

    assert value == pytest.approx(0.15)


def test_top_of_book_ofi_matches_expected_cont_style_update() -> None:
    previous_tick = _make_tick(
        offset_seconds=0,
        bid=1.1000,
        ask=1.1002,
        bid_size=2.0,
        ask_size=3.0,
    )
    current_tick = _make_tick(
        offset_seconds=1,
        bid=1.1001,
        ask=1.1002,
        bid_size=4.0,
        ask_size=2.0,
    )

    assert top_of_book_ofi(previous_tick, current_tick) == pytest.approx(5.0)


def test_multi_level_ofi_returns_flat_feature_mapping() -> None:
    previous_book = _make_book(
        offset_seconds=0,
        bid_prices=[1.1000, 1.0999],
        ask_prices=[1.1002, 1.1003],
        bid_sizes=[2.0, 1.0],
        ask_sizes=[3.0, 1.0],
    )
    current_book = _make_book(
        offset_seconds=1,
        bid_prices=[1.1001, 1.0999],
        ask_prices=[1.1002, 1.1003],
        bid_sizes=[4.0, 2.0],
        ask_sizes=[2.0, 3.0],
    )

    result = multi_level_ofi(previous_book, current_book, depth=2)

    assert result == pytest.approx({"mlofi_l1": 5.0, "mlofi_l2": -1.0, "mlofi_total": 4.0})


def test_toxicity_proxy_returns_normalized_signed_imbalance() -> None:
    value = toxicity_vpin_proxy([2.0, -1.0, 1.0], [2.0, 1.0, 1.0])

    assert value == pytest.approx(0.5)


def _make_tick(
    *,
    offset_seconds: int,
    bid: float,
    ask: float,
    bid_size: float,
    ask_size: float,
    last_price: float | None = None,
    last_size: float | None = None,
) -> TickEvent:
    event_timestamp = datetime(2026, 3, 26, 9, 0, 0, tzinfo=UTC) + timedelta(
        seconds=offset_seconds
    )
    return TickEvent(
        symbol="EURUSD",
        venue="TEST",
        event_timestamp=event_timestamp,
        received_timestamp=event_timestamp,
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
    bid_prices: list[float],
    ask_prices: list[float],
    bid_sizes: list[float],
    ask_sizes: list[float],
) -> BookSnapshot:
    event_timestamp = datetime(2026, 3, 26, 9, 0, 0, tzinfo=UTC) + timedelta(
        seconds=offset_seconds
    )
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
        received_timestamp=event_timestamp,
        bids=bids,
        asks=asks,
    )

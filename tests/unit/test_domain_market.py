"""Unit tests for canonical market data models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from scalper_ai.domain import BookLevel, BookSide, BookSnapshot, EventSource, TickEvent


def test_tick_event_normalizes_aware_timestamps_to_utc() -> None:
    event = TickEvent(
        symbol="EURUSD",
        venue="MT5",
        event_timestamp=datetime(2026, 3, 26, 12, 0, tzinfo=timezone(timedelta(hours=3))),
        received_timestamp=datetime(2026, 3, 26, 12, 0, 1, tzinfo=timezone(timedelta(hours=3))),
        bid=1.0812,
        ask=1.0813,
        bid_size=2.0,
        ask_size=1.5,
        sequence=101,
        source=EventSource.REPLAY,
    )

    assert event.event_timestamp.tzinfo == UTC
    assert event.event_timestamp.hour == 9
    assert event.received_timestamp.tzinfo == UTC


def test_tick_event_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        TickEvent(
            symbol="EURUSD",
            venue="MT5",
            event_timestamp=datetime(2026, 3, 26, 12, 0),
            received_timestamp=datetime(2026, 3, 26, 12, 0, 1, tzinfo=UTC),
            bid=1.0812,
            ask=1.0813,
        )


def test_tick_event_rejects_negative_spread() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to bid"):
        TickEvent(
            symbol="EURUSD",
            venue="MT5",
            event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            received_timestamp=datetime(2026, 3, 26, 9, 0, 1, tzinfo=UTC),
            bid=1.0814,
            ask=1.0813,
        )


def test_book_snapshot_rejects_unsorted_bid_prices() -> None:
    with pytest.raises(ValidationError, match="descending price"):
        BookSnapshot(
            symbol="EURUSD",
            venue="MT5",
            event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            received_timestamp=datetime(2026, 3, 26, 9, 0, 0, 500000, tzinfo=UTC),
            bids=[
                BookLevel(side=BookSide.BID, level=1, price=1.0811, size=1.0),
                BookLevel(side=BookSide.BID, level=2, price=1.0812, size=2.0),
            ],
            asks=[
                BookLevel(side=BookSide.ASK, level=1, price=1.0813, size=1.2),
                BookLevel(side=BookSide.ASK, level=2, price=1.0814, size=2.1),
            ],
        )


def test_book_snapshot_rejects_duplicate_levels() -> None:
    with pytest.raises(ValidationError, match="unique level numbers"):
        BookSnapshot(
            symbol="EURUSD",
            venue="MT5",
            event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            received_timestamp=datetime(2026, 3, 26, 9, 0, 0, 500000, tzinfo=UTC),
            bids=[
                BookLevel(side=BookSide.BID, level=1, price=1.0812, size=1.0),
                BookLevel(side=BookSide.BID, level=1, price=1.0811, size=2.0),
            ],
            asks=[
                BookLevel(side=BookSide.ASK, level=1, price=1.0813, size=1.2),
                BookLevel(side=BookSide.ASK, level=2, price=1.0814, size=2.1),
            ],
        )


def test_book_snapshot_rejects_crossed_market() -> None:
    with pytest.raises(ValidationError, match="Top ask"):
        BookSnapshot(
            symbol="EURUSD",
            venue="MT5",
            event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            received_timestamp=datetime(2026, 3, 26, 9, 0, 0, 500000, tzinfo=UTC),
            bids=[BookLevel(side=BookSide.BID, level=1, price=1.0815, size=1.0)],
            asks=[BookLevel(side=BookSide.ASK, level=1, price=1.0814, size=1.2)],
        )

"""Unit tests for bar builders built on canonical tick events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scalper_ai.data.bar_builders import (
    ImbalanceBarBuilder,
    TickBarBuilder,
    TimeBarBuilder,
    VolatilityBarBuilder,
    build_bars,
)
from scalper_ai.domain import BarType, TickEvent


def make_tick(
    second: int,
    *,
    bid: float,
    ask: float,
    last_price: float | None = None,
    last_size: float | None = None,
) -> TickEvent:
    timestamp = datetime(2026, 3, 26, 9, 0, 0, tzinfo=UTC) + timedelta(seconds=second)
    return TickEvent(
        symbol="EURUSD",
        venue="REPLAY",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=bid,
        ask=ask,
        last_price=last_price,
        last_size=last_size,
    )


def test_time_bar_builder_groups_ticks_by_interval() -> None:
    ticks = [
        make_tick(0, bid=1.0812, ask=1.0813),
        make_tick(10, bid=1.08125, ask=1.08135),
        make_tick(61, bid=1.0814, ask=1.0815),
    ]

    bars = build_bars(TimeBarBuilder(interval=timedelta(minutes=1)), ticks)

    assert len(bars) == 2
    assert bars[0].bar_type == BarType.TIME
    assert bars[0].tick_count == 2
    assert bars[0].start_timestamp == datetime(2026, 3, 26, 9, 0, tzinfo=UTC)
    assert bars[1].start_timestamp == datetime(2026, 3, 26, 9, 1, tzinfo=UTC)


def test_tick_bar_builder_emits_fixed_size_bars() -> None:
    ticks = [
        make_tick(0, bid=1.0812, ask=1.0813),
        make_tick(1, bid=1.08121, ask=1.08131),
        make_tick(2, bid=1.08122, ask=1.08132),
        make_tick(3, bid=1.08123, ask=1.08133),
        make_tick(4, bid=1.08124, ask=1.08134),
    ]

    bars = build_bars(TickBarBuilder(ticks_per_bar=2), ticks)

    assert len(bars) == 3
    assert [bar.tick_count for bar in bars] == [2, 2, 1]
    assert all(bar.bar_type == BarType.TICK for bar in bars)


def test_volatility_bar_builder_closes_when_threshold_hit() -> None:
    ticks = [
        make_tick(0, bid=1.0000, ask=1.0002),
        make_tick(1, bid=1.0010, ask=1.0012),
        make_tick(2, bid=1.0025, ask=1.0027),
    ]

    bars = build_bars(VolatilityBarBuilder(threshold=0.001), ticks)

    assert len(bars) >= 1
    assert bars[0].bar_type == BarType.VOLATILITY
    assert bars[0].tick_count >= 2


def test_imbalance_bar_builder_uses_tick_rule_and_threshold() -> None:
    ticks = [
        make_tick(0, bid=1.0812, ask=1.0813),
        make_tick(1, bid=1.0813, ask=1.0814),
        make_tick(2, bid=1.0814, ask=1.0815),
    ]

    bars = build_bars(ImbalanceBarBuilder(imbalance_threshold=2.0), ticks)

    assert len(bars) >= 1
    assert bars[0].bar_type == BarType.IMBALANCE
    assert bars[0].imbalance is not None
    assert bars[0].imbalance >= 2.0

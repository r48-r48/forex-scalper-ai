"""Order-flow imbalance helpers for top-of-book and multi-level books."""

from __future__ import annotations

from typing import Sequence, Union

from scalper_ai.domain import BookLevel, BookSnapshot, TickEvent
from scalper_ai.features.primitives import best_bid_ask, best_sizes
from scalper_ai.features.schema import MLOFI_TOTAL_FEATURE, mlofi_feature_name

TopOfBookEvent = Union[TickEvent, BookSnapshot]


def top_of_book_ofi(previous: TopOfBookEvent, current: TopOfBookEvent) -> float:
    """Return Cont-style order flow imbalance between two top-of-book states."""

    previous_bid, previous_ask = best_bid_ask(previous)
    current_bid, current_ask = best_bid_ask(current)
    previous_bid_size, previous_ask_size = best_sizes(previous)
    current_bid_size, current_ask_size = best_sizes(current)

    bid_component = (
        float(current_bid >= previous_bid) * current_bid_size
        - float(current_bid <= previous_bid) * previous_bid_size
    )
    ask_component = (
        float(current_ask <= previous_ask) * current_ask_size
        - float(current_ask >= previous_ask) * previous_ask_size
    )
    return bid_component - ask_component


def rolling_ofi(increments: Sequence[float]) -> float:
    """Aggregate raw OFI increments into a trailing OFI value."""

    return float(sum(increments))


def multi_level_ofi(previous: BookSnapshot, current: BookSnapshot, *, depth: int) -> dict[str, float]:
    """Return flat MLOFI features for a top-N book depth."""

    if depth <= 0:
        raise ValueError("depth must be greater than zero.")

    previous_bids = {level.level: level for level in previous.bids if level.level <= depth}
    previous_asks = {level.level: level for level in previous.asks if level.level <= depth}
    current_bids = {level.level: level for level in current.bids if level.level <= depth}
    current_asks = {level.level: level for level in current.asks if level.level <= depth}

    values: dict[str, float] = {}
    total = 0.0
    for level_number in range(1, depth + 1):
        bid_contribution = _level_ofi_contribution(
            previous_bids.get(level_number),
            current_bids.get(level_number),
            is_bid=True,
        )
        ask_contribution = _level_ofi_contribution(
            previous_asks.get(level_number),
            current_asks.get(level_number),
            is_bid=False,
        )
        level_value = bid_contribution + ask_contribution
        values[mlofi_feature_name(level_number)] = level_value
        total += level_value

    values[MLOFI_TOTAL_FEATURE] = total
    return values


def empty_mlofi(*, depth: int) -> dict[str, float]:
    """Return a zero-valued MLOFI feature mapping."""

    if depth <= 0:
        raise ValueError("depth must be greater than zero.")
    values = {mlofi_feature_name(level): 0.0 for level in range(1, depth + 1)}
    values[MLOFI_TOTAL_FEATURE] = 0.0
    return values


def _level_ofi_contribution(
    previous_level: BookLevel | None,
    current_level: BookLevel | None,
    *,
    is_bid: bool,
) -> float:
    previous_price, previous_size = _level_price_size(previous_level, is_bid=is_bid, is_previous=True)
    current_price, current_size = _level_price_size(current_level, is_bid=is_bid, is_previous=False)
    if is_bid:
        return (
            float(current_price >= previous_price) * current_size
            - float(current_price <= previous_price) * previous_size
        )
    return (
        -float(current_price <= previous_price) * current_size
        + float(current_price >= previous_price) * previous_size
    )


def _level_price_size(
    level: BookLevel | None,
    *,
    is_bid: bool,
    is_previous: bool,
) -> tuple[float, float]:
    if level is not None:
        return float(level.price), float(level.size)
    if is_bid:
        return 0.0, 0.0
    if is_previous:
        return float("inf"), 0.0
    return float("inf"), 0.0

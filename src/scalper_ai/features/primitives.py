"""Pure primitive feature functions for top-of-book microstructure features."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Sequence, Union

import numpy as np

from scalper_ai.domain import BookSnapshot, TickEvent

TopOfBookEvent = Union[TickEvent, BookSnapshot]


def best_bid_ask(event: TopOfBookEvent) -> tuple[float, float]:
    """Return the best bid and ask from a tick or book event."""

    if isinstance(event, TickEvent):
        return float(event.bid), float(event.ask)
    return float(event.bids[0].price), float(event.asks[0].price)


def best_sizes(event: TopOfBookEvent) -> tuple[float, float]:
    """Return the best bid and ask sizes from a tick or book event."""

    if isinstance(event, TickEvent):
        return float(event.bid_size or 0.0), float(event.ask_size or 0.0)
    return float(event.bids[0].size), float(event.asks[0].size)


def mid_price(event: TopOfBookEvent) -> float:
    """Return the top-of-book mid price."""

    bid, ask = best_bid_ask(event)
    return (bid + ask) / 2.0


def spread(event: TopOfBookEvent) -> float:
    """Return the top-of-book spread."""

    bid, ask = best_bid_ask(event)
    return ask - bid


def spread_bps(event: TopOfBookEvent) -> float:
    """Return the spread normalized by mid price in basis points."""

    current_mid = mid_price(event)
    if current_mid <= 0:
        raise ValueError("mid price must be positive to compute spread_bps.")
    return (spread(event) / current_mid) * 10_000.0


def log_mid_return(previous_mid: float, current_mid: float) -> float:
    """Return the log return between two strictly positive mid prices."""

    if previous_mid <= 0 or current_mid <= 0:
        raise ValueError("mid prices must be positive to compute returns.")
    return float(math.log(current_mid / previous_mid))


def realized_volatility(log_returns: Sequence[float]) -> float:
    """Return square-root summed realized volatility over a trailing window."""

    if not log_returns:
        return 0.0
    array = np.asarray(log_returns, dtype=np.float64)
    return float(np.sqrt(np.square(array).sum()))


def quote_intensity(
    event_timestamps: Sequence[datetime],
    *,
    now: datetime,
    window_seconds: float,
) -> float:
    """Return trailing quote update intensity as updates per second."""

    if window_seconds <= 0:
        raise ValueError("window_seconds must be greater than zero.")
    window = timedelta(seconds=window_seconds)
    count = 0
    for timestamp in event_timestamps:
        delta = now - timestamp
        if timedelta(0) <= delta <= window:
            count += 1
    return count / window_seconds

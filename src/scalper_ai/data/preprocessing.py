"""Pure preprocessing helpers for tick-derived transformations."""

from __future__ import annotations

from typing import Sequence

import numpy as np

from scalper_ai.domain import TickEvent


def mid_price(tick: TickEvent) -> float:
    """Return the top-of-book mid price for a tick."""

    return (tick.bid + tick.ask) / 2.0


def trade_proxy_price(tick: TickEvent) -> float:
    """Return a proxy trade price using last price when available, else mid."""

    return tick.last_price if tick.last_price is not None else mid_price(tick)


def volume_proxy(tick: TickEvent) -> float:
    """Return a stable size proxy for tick-based preprocessing pipelines."""

    if tick.last_size is not None and tick.last_size > 0:
        return tick.last_size
    sizes = [size for size in (tick.bid_size, tick.ask_size) if size is not None]
    if sizes:
        return sum(sizes) / len(sizes)
    return 1.0


def fractional_diff_weights(order: float, max_terms: int, threshold: float = 1e-5) -> np.ndarray:
    """Generate truncated fractional differentiation weights."""

    if max_terms <= 0:
        raise ValueError("max_terms must be greater than zero.")
    if threshold < 0:
        raise ValueError("threshold must be non-negative.")

    weights = [1.0]
    for k in range(1, max_terms):
        next_weight = -weights[-1] * ((order - k + 1) / k)
        if abs(next_weight) < threshold:
            break
        weights.append(next_weight)
    return np.asarray(weights, dtype=np.float64)


def fractional_differentiate(
    values: Sequence[float],
    order: float,
    threshold: float = 1e-5,
) -> np.ndarray:
    """Apply fixed-width fractional differentiation to a 1D numeric sequence."""

    series = np.asarray(values, dtype=np.float64)
    if series.ndim != 1:
        raise ValueError("fractional_differentiate expects a 1D sequence.")
    if series.size == 0:
        return np.asarray([], dtype=np.float64)

    weights = fractional_diff_weights(order=order, max_terms=series.size, threshold=threshold)
    width = int(weights.size)
    result = np.full(series.shape[0], np.nan, dtype=np.float64)
    reversed_weights = weights[::-1]
    for index in range(width - 1, series.shape[0]):
        window = series[index - width + 1 : index + 1]
        result[index] = float(np.dot(reversed_weights, window))
    return result

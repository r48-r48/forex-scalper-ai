"""VPIN-like toxicity proxy helpers for tick-driven flows."""

from __future__ import annotations

from collections.abc import Sequence

from scalper_ai.data.preprocessing import trade_proxy_price, volume_proxy
from scalper_ai.domain import TickEvent


def tick_rule_sign(
    current_price: float,
    *,
    previous_price: float | None,
    previous_sign: int = 1,
) -> int:
    """Classify trade direction using a simple tick rule."""

    if previous_price is None:
        return 1 if previous_sign >= 0 else -1
    if current_price > previous_price:
        return 1
    if current_price < previous_price:
        return -1
    return 1 if previous_sign >= 0 else -1


def signed_trade_volume(
    tick: TickEvent,
    *,
    previous_trade_price: float | None,
    previous_sign: int = 1,
) -> tuple[float, float, int]:
    """Return volume, signed volume, and updated sign for a tick event."""

    proxy_price = trade_proxy_price(tick)
    volume = float(volume_proxy(tick))
    sign = tick_rule_sign(
        proxy_price,
        previous_price=previous_trade_price,
        previous_sign=previous_sign,
    )
    return volume, volume * sign, sign


def toxicity_vpin_proxy(
    signed_volumes: Sequence[float],
    absolute_volumes: Sequence[float],
) -> float:
    """Return a VPIN-like toxicity proxy from trailing signed flow imbalance."""

    total_volume = float(sum(absolute_volumes))
    if total_volume <= 0:
        return 0.0
    imbalance = abs(float(sum(signed_volumes)))
    return imbalance / total_volume

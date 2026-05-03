"""Canonical aggregated bar models used by preprocessing pipelines."""

from __future__ import annotations

from typing import Any

from pydantic import FiniteFloat, model_validator

from scalper_ai.domain.base import DomainModel
from scalper_ai.domain.enums import BarType
from scalper_ai.domain.validators import (
    NonEmptyStr,
    NonNegativeFiniteFloat,
    PositiveFiniteFloat,
    PositiveInt,
    UtcDatetime,
)


class BarEvent(DomainModel):
    """Canonical aggregated bar built from tick data."""

    symbol: NonEmptyStr
    venue: NonEmptyStr
    bar_type: BarType
    start_timestamp: UtcDatetime
    end_timestamp: UtcDatetime
    open: PositiveFiniteFloat
    high: PositiveFiniteFloat
    low: PositiveFiniteFloat
    close: PositiveFiniteFloat
    volume: NonNegativeFiniteFloat = 0.0
    tick_count: PositiveInt
    notional: NonNegativeFiniteFloat = 0.0
    vwap: PositiveFiniteFloat | None = None
    buy_volume: NonNegativeFiniteFloat = 0.0
    sell_volume: NonNegativeFiniteFloat = 0.0
    imbalance: FiniteFloat | None = None
    metadata: dict[NonEmptyStr, Any] | None = None

    @model_validator(mode="after")
    def validate_ohlcv(self) -> BarEvent:
        if self.end_timestamp < self.start_timestamp:
            raise ValueError("Bar end_timestamp must be greater than or equal to start_timestamp.")
        if self.high < max(self.open, self.close):
            raise ValueError("Bar high must be greater than or equal to open and close.")
        if self.low > min(self.open, self.close):
            raise ValueError("Bar low must be less than or equal to open and close.")
        if self.low > self.high:
            raise ValueError("Bar low must not exceed high.")
        if self.vwap is not None and not (self.low <= self.vwap <= self.high):
            raise ValueError("Bar vwap must lie within [low, high].")
        return self

"""Canonical market data events."""

from __future__ import annotations

from pydantic import Field, model_validator

from scalper_ai.domain.base import DomainModel
from scalper_ai.domain.enums import BookSide, EventSource
from scalper_ai.domain.validators import (
    NonEmptyStr,
    NonNegativeFiniteFloat,
    NonNegativeInt,
    PositiveFiniteFloat,
    PositiveInt,
    UtcDatetime,
)


class TickEvent(DomainModel):
    """Top-of-book tick event."""

    symbol: NonEmptyStr
    venue: NonEmptyStr
    event_timestamp: UtcDatetime
    received_timestamp: UtcDatetime
    bid: PositiveFiniteFloat
    ask: PositiveFiniteFloat
    bid_size: NonNegativeFiniteFloat | None = None
    ask_size: NonNegativeFiniteFloat | None = None
    last_price: PositiveFiniteFloat | None = None
    last_size: NonNegativeFiniteFloat | None = None
    sequence: NonNegativeInt | None = None
    source: EventSource | None = None

    @model_validator(mode="after")
    def validate_spread(self) -> TickEvent:
        if self.ask < self.bid:
            raise ValueError("TickEvent ask must be greater than or equal to bid.")
        return self


class BookLevel(DomainModel):
    """Single level inside a book snapshot."""

    side: BookSide
    level: PositiveInt
    price: PositiveFiniteFloat
    size: PositiveFiniteFloat
    order_count: NonNegativeInt | None = None


class BookSnapshot(DomainModel):
    """Canonical full or truncated top-N order book snapshot."""

    symbol: NonEmptyStr
    venue: NonEmptyStr
    event_timestamp: UtcDatetime
    received_timestamp: UtcDatetime
    sequence: NonNegativeInt | None = None
    bids: list[BookLevel] = Field(min_length=1)
    asks: list[BookLevel] = Field(min_length=1)
    checksum: NonEmptyStr | None = None
    is_full_snapshot: bool = True

    @model_validator(mode="after")
    def validate_book_structure(self) -> BookSnapshot:
        if any(level.side != BookSide.BID for level in self.bids):
            raise ValueError("All bid levels must use BookSide.BID.")
        if any(level.side != BookSide.ASK for level in self.asks):
            raise ValueError("All ask levels must use BookSide.ASK.")

        bid_levels = [level.level for level in self.bids]
        ask_levels = [level.level for level in self.asks]
        if len(set(bid_levels)) != len(bid_levels):
            raise ValueError("Bid levels must have unique level numbers.")
        if len(set(ask_levels)) != len(ask_levels):
            raise ValueError("Ask levels must have unique level numbers.")

        bid_prices = [level.price for level in self.bids]
        ask_prices = [level.price for level in self.asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("Bid levels must be sorted by descending price.")
        if ask_prices != sorted(ask_prices):
            raise ValueError("Ask levels must be sorted by ascending price.")
        if ask_prices[0] < bid_prices[0]:
            raise ValueError("Top ask must be greater than or equal to top bid.")

        return self

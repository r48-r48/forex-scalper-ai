"""Configuration contracts for deterministic historical backtests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration for the PHASE 9 event-driven backtesting engine."""

    price_column: str = "mid_price"
    available_timestamp_column: str = "available_timestamp"
    event_timestamp_column: str = "event_timestamp"
    symbol_column: str = "symbol"
    bid_price_column: str | None = None
    ask_price_column: str | None = None
    initial_cash: float = 100_000.0
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    commission_bps: float = 0.0

    def __post_init__(self) -> None:
        if not self.price_column.strip():
            raise ValueError("price_column must be non-empty.")
        if not self.available_timestamp_column.strip():
            raise ValueError("available_timestamp_column must be non-empty.")
        if not self.event_timestamp_column.strip():
            raise ValueError("event_timestamp_column must be non-empty.")
        if not self.symbol_column.strip():
            raise ValueError("symbol_column must be non-empty.")
        if (self.bid_price_column is None) != (self.ask_price_column is None):
            raise ValueError("bid_price_column and ask_price_column must be configured together.")
        if self.bid_price_column is not None and not self.bid_price_column.strip():
            raise ValueError("bid_price_column must be non-empty when provided.")
        if self.ask_price_column is not None and not self.ask_price_column.strip():
            raise ValueError("ask_price_column must be non-empty when provided.")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be greater than zero.")
        if self.spread_bps < 0:
            raise ValueError("spread_bps must be non-negative.")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative.")
        if self.commission_bps < 0:
            raise ValueError("commission_bps must be non-negative.")

    @property
    def uses_bid_ask_execution(self) -> bool:
        """Return whether market fills should use side-specific bid/ask prices."""

        return self.bid_price_column is not None and self.ask_price_column is not None

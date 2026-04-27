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
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be greater than zero.")
        if self.spread_bps < 0:
            raise ValueError("spread_bps must be non-negative.")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative.")
        if self.commission_bps < 0:
            raise ValueError("commission_bps must be non-negative.")

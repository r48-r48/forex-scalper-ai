"""Configuration contracts for deterministic historical backtests."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FxSymbolSpec:
    """Broker-style FX symbol assumptions used by opt-in historical realism metrics."""

    base_currency: str
    quote_currency: str
    account_currency: str
    pip_size: float
    contract_size: float = 100_000.0
    quote_to_account_rate: float = 1.0
    margin_rate: float = 0.0
    swap_long_per_lot: float = 0.0
    swap_short_per_lot: float = 0.0
    rollover_hour_utc: int = 21

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_currency", _currency_code(self.base_currency))
        object.__setattr__(self, "quote_currency", _currency_code(self.quote_currency))
        object.__setattr__(self, "account_currency", _currency_code(self.account_currency))
        _require_positive_finite(self.pip_size, "pip_size")
        _require_positive_finite(self.contract_size, "contract_size")
        _require_positive_finite(self.quote_to_account_rate, "quote_to_account_rate")
        _require_non_negative_finite(self.margin_rate, "margin_rate")
        _require_finite(self.swap_long_per_lot, "swap_long_per_lot")
        _require_finite(self.swap_short_per_lot, "swap_short_per_lot")
        if self.rollover_hour_utc < 0 or self.rollover_hour_utc > 23:
            raise ValueError("rollover_hour_utc must be between 0 and 23.")

    @property
    def pip_value_per_unit(self) -> float:
        """Return account-currency value of one pip for one base unit."""

        return self.pip_size * self.quote_to_account_rate

    @property
    def pip_value_per_lot(self) -> float:
        """Return account-currency value of one pip for one broker lot."""

        return self.pip_value_per_unit * self.contract_size


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration for the PHASE 9 event-driven backtesting engine."""

    price_column: str = "mid_price"
    available_timestamp_column: str = "available_timestamp"
    event_timestamp_column: str = "event_timestamp"
    symbol_column: str = "symbol"
    bid_price_column: str | None = None
    ask_price_column: str | None = None
    high_price_column: str | None = None
    low_price_column: str | None = None
    initial_cash: float = 100_000.0
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    commission_bps: float = 0.0
    spread_bps_column: str | None = None
    slippage_bps_column: str | None = None
    commission_bps_column: str | None = None
    fx_symbol: FxSymbolSpec | None = None
    margin_call_level: float | None = None
    stop_loss_price_column: str | None = None
    take_profit_price_column: str | None = None
    protective_exit_priority: str = "stop_loss"

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
        if (self.high_price_column is None) != (self.low_price_column is None):
            raise ValueError("high_price_column and low_price_column must be configured together.")
        _validate_optional_column_name(self.high_price_column, "high_price_column")
        _validate_optional_column_name(self.low_price_column, "low_price_column")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be greater than zero.")
        if self.spread_bps < 0:
            raise ValueError("spread_bps must be non-negative.")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative.")
        if self.commission_bps < 0:
            raise ValueError("commission_bps must be non-negative.")
        _validate_optional_column_name(self.spread_bps_column, "spread_bps_column")
        _validate_optional_column_name(self.slippage_bps_column, "slippage_bps_column")
        _validate_optional_column_name(self.commission_bps_column, "commission_bps_column")
        if self.margin_call_level is not None:
            _require_positive_finite(self.margin_call_level, "margin_call_level")
        _validate_optional_column_name(self.stop_loss_price_column, "stop_loss_price_column")
        _validate_optional_column_name(
            self.take_profit_price_column,
            "take_profit_price_column",
        )
        if self.uses_protective_exit_simulation and not self.uses_bar_path:
            raise ValueError(
                "high_price_column and low_price_column are required when "
                "stop_loss_price_column or take_profit_price_column is configured."
            )
        if self.protective_exit_priority not in {"stop_loss", "take_profit"}:
            raise ValueError("protective_exit_priority must be 'stop_loss' or 'take_profit'.")

    @property
    def uses_bid_ask_execution(self) -> bool:
        """Return whether market fills should use side-specific bid/ask prices."""

        return self.bid_price_column is not None and self.ask_price_column is not None

    @property
    def uses_bar_path(self) -> bool:
        """Return whether high/low bar path columns are configured."""

        return self.high_price_column is not None and self.low_price_column is not None

    @property
    def uses_protective_exit_simulation(self) -> bool:
        """Return whether stop-loss or take-profit path simulation is enabled."""

        return self.stop_loss_price_column is not None or self.take_profit_price_column is not None


def _currency_code(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("currency code must be non-empty.")
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise ValueError("currency code must be a three-letter ISO-like code.")
    return normalized


def _validate_optional_column_name(value: str | None, field_name: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{field_name} must be non-empty when provided.")


def _require_positive_finite(value: float, field_name: str) -> None:
    _require_finite(value, field_name)
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")


def _require_non_negative_finite(value: float, field_name: str) -> None:
    _require_finite(value, field_name)
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative.")


def _require_finite(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be numeric.")
    if not math.isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite.")

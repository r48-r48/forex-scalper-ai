"""Baseline target-position strategies for replay and walk-forward validation."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from scalper_ai.backtesting.engine import BacktestEvent, BacktestState, TargetPositionStrategy

_ZERO_TOLERANCE = 1e-12


@dataclass(frozen=True)
class BaselineRiskConfig:
    """Shared risk limits for simple baseline strategies."""

    max_abs_position: float = 1.0
    max_spread_bps: float | None = None
    spread_bps_column: str = "spread_bps"

    def __post_init__(self) -> None:
        if self.max_abs_position <= 0:
            raise ValueError("max_abs_position must be greater than zero.")
        if self.max_spread_bps is not None and self.max_spread_bps < 0:
            raise ValueError("max_spread_bps must be non-negative when provided.")
        if not self.spread_bps_column.strip():
            raise ValueError("spread_bps_column must be non-empty.")


@dataclass(frozen=True)
class SpreadMeanReversionConfig:
    """Configuration for a return/spread-aware mean-reversion baseline."""

    risk: BaselineRiskConfig = BaselineRiskConfig(max_spread_bps=2.0)
    return_column: str = "mid_return"
    entry_return_threshold: float = 0.00025
    exit_return_threshold: float = 0.00005

    def __post_init__(self) -> None:
        if not self.return_column.strip():
            raise ValueError("return_column must be non-empty.")
        if self.entry_return_threshold <= 0:
            raise ValueError("entry_return_threshold must be greater than zero.")
        if self.exit_return_threshold < 0:
            raise ValueError("exit_return_threshold must be non-negative.")
        if self.exit_return_threshold > self.entry_return_threshold:
            raise ValueError("exit_return_threshold must not exceed entry_return_threshold.")


@dataclass(frozen=True)
class OfiImbalanceConfig:
    """Configuration for an order-flow imbalance baseline."""

    risk: BaselineRiskConfig = BaselineRiskConfig(max_spread_bps=2.0)
    signal_columns: tuple[str, ...] = ("ofi", "mlofi_total")
    entry_threshold: float = 1.0
    exit_threshold: float = 0.25

    def __post_init__(self) -> None:
        if not self.signal_columns:
            raise ValueError("signal_columns must contain at least one column.")
        if any(not column.strip() for column in self.signal_columns):
            raise ValueError("signal_columns must contain non-empty names.")
        if self.entry_threshold <= 0:
            raise ValueError("entry_threshold must be greater than zero.")
        if self.exit_threshold < 0:
            raise ValueError("exit_threshold must be non-negative.")
        if self.exit_threshold > self.entry_threshold:
            raise ValueError("exit_threshold must not exceed entry_threshold.")


@dataclass(frozen=True)
class VolatilityBreakoutConfig:
    """Configuration for a volatility-normalized momentum baseline."""

    risk: BaselineRiskConfig = BaselineRiskConfig(max_spread_bps=2.0)
    return_column: str = "mid_return"
    volatility_column: str = "realized_volatility"
    absolute_return_threshold: float = 0.00025
    entry_volatility_multiplier: float = 1.5
    exit_volatility_multiplier: float = 0.5

    def __post_init__(self) -> None:
        if not self.return_column.strip():
            raise ValueError("return_column must be non-empty.")
        if not self.volatility_column.strip():
            raise ValueError("volatility_column must be non-empty.")
        if self.absolute_return_threshold <= 0:
            raise ValueError("absolute_return_threshold must be greater than zero.")
        if self.entry_volatility_multiplier <= 0:
            raise ValueError("entry_volatility_multiplier must be greater than zero.")
        if self.exit_volatility_multiplier < 0:
            raise ValueError("exit_volatility_multiplier must be non-negative.")
        if self.exit_volatility_multiplier > self.entry_volatility_multiplier:
            raise ValueError(
                "exit_volatility_multiplier must not exceed entry_volatility_multiplier."
            )


@dataclass(frozen=True)
class BaselineStrategySpec:
    """Named baseline strategy factory for reports and walk-forward loops."""

    name: str
    factory: Callable[[], TargetPositionStrategy]
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Baseline strategy name must be non-empty.")

    def build(self) -> TargetPositionStrategy:
        """Return a fresh strategy instance for one independent backtest run."""

        return self.factory()


class SpreadMeanReversionStrategy:
    """Short sharp upward moves and buy sharp downward moves when spread allows it."""

    strategy_id = "baseline_spread_mean_reversion"

    def __init__(self, config: SpreadMeanReversionConfig | None = None) -> None:
        self.config = config or SpreadMeanReversionConfig()

    def __call__(self, event: BacktestEvent, state: BacktestState) -> float:
        if not _spread_allows_entry(event, self.config.risk):
            return 0.0

        latest_return = _numeric_value(event, self.config.return_column)
        if latest_return is None:
            return 0.0

        magnitude = abs(latest_return)
        if magnitude >= self.config.entry_return_threshold:
            direction = -1.0 if latest_return > 0 else 1.0
            return direction * self.config.risk.max_abs_position
        if magnitude <= self.config.exit_return_threshold:
            return 0.0
        return _bounded_current_position(state, self.config.risk.max_abs_position)


class OfiImbalanceStrategy:
    """Follow current order-flow pressure while gracefully staying flat without OFI data."""

    strategy_id = "baseline_ofi_imbalance"

    def __init__(self, config: OfiImbalanceConfig | None = None) -> None:
        self.config = config or OfiImbalanceConfig()

    def __call__(self, event: BacktestEvent, state: BacktestState) -> float:
        if not _spread_allows_entry(event, self.config.risk):
            return 0.0

        signal = _first_numeric_value(event, self.config.signal_columns)
        if signal is None:
            return 0.0

        magnitude = abs(signal)
        if magnitude >= self.config.entry_threshold:
            direction = 1.0 if signal > 0 else -1.0
            return direction * self.config.risk.max_abs_position
        if magnitude <= self.config.exit_threshold:
            return 0.0
        return _bounded_current_position(state, self.config.risk.max_abs_position)


class VolatilityBreakoutStrategy:
    """Follow returns that break out beyond a current volatility-adjusted threshold."""

    strategy_id = "baseline_volatility_breakout"

    def __init__(self, config: VolatilityBreakoutConfig | None = None) -> None:
        self.config = config or VolatilityBreakoutConfig()

    def __call__(self, event: BacktestEvent, state: BacktestState) -> float:
        if not _spread_allows_entry(event, self.config.risk):
            return 0.0

        latest_return = _numeric_value(event, self.config.return_column)
        if latest_return is None:
            return 0.0

        volatility = _numeric_value(event, self.config.volatility_column)
        entry_threshold = self.config.absolute_return_threshold
        exit_threshold = self.config.absolute_return_threshold * 0.5
        if volatility is not None and volatility > _ZERO_TOLERANCE:
            entry_threshold = max(
                entry_threshold,
                volatility * self.config.entry_volatility_multiplier,
            )
            exit_threshold = volatility * self.config.exit_volatility_multiplier

        magnitude = abs(latest_return)
        if magnitude >= entry_threshold:
            direction = 1.0 if latest_return > 0 else -1.0
            return direction * self.config.risk.max_abs_position
        if magnitude <= exit_threshold:
            return 0.0
        return _bounded_current_position(state, self.config.risk.max_abs_position)


def build_default_baseline_specs(
    *,
    max_abs_position: float = 1.0,
    max_spread_bps: float | None = 2.0,
) -> tuple[BaselineStrategySpec, ...]:
    """Return the default P1.2 baseline suite with explicit position and spread limits."""

    risk = BaselineRiskConfig(
        max_abs_position=max_abs_position,
        max_spread_bps=max_spread_bps,
    )
    return (
        BaselineStrategySpec(
            name="spread_mean_reversion",
            factory=lambda: SpreadMeanReversionStrategy(
                SpreadMeanReversionConfig(risk=risk),
            ),
            description="Mean-revert current mid-return when spread is within limit.",
        ),
        BaselineStrategySpec(
            name="ofi_imbalance",
            factory=lambda: OfiImbalanceStrategy(OfiImbalanceConfig(risk=risk)),
            description="Follow current OFI/MLOFI pressure with a flat fallback.",
        ),
        BaselineStrategySpec(
            name="volatility_breakout",
            factory=lambda: VolatilityBreakoutStrategy(VolatilityBreakoutConfig(risk=risk)),
            description="Follow current return breakouts above realized volatility.",
        ),
    )


def _spread_allows_entry(event: BacktestEvent, risk: BaselineRiskConfig) -> bool:
    if risk.max_spread_bps is None:
        return True
    spread_bps = _numeric_value(event, risk.spread_bps_column)
    if spread_bps is None:
        return True
    return spread_bps <= risk.max_spread_bps


def _first_numeric_value(event: BacktestEvent, columns: tuple[str, ...]) -> float | None:
    for column in columns:
        value = _numeric_value(event, column)
        if value is not None:
            return value
    return None


def _numeric_value(event: BacktestEvent, column: str) -> float | None:
    if column not in event.row_payload:
        return None
    value = event.row_payload[column]
    if value is None or pd.isna(value):
        return None
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError(f"Baseline signal column '{column}' must be finite.")
    return numeric_value


def _bounded_current_position(state: BacktestState, max_abs_position: float) -> float:
    if state.current_position is None:
        return 0.0
    current_position = float(state.current_position.net_quantity)
    if math.isclose(current_position, 0.0, abs_tol=_ZERO_TOLERANCE):
        return 0.0
    return max(-max_abs_position, min(max_abs_position, current_position))

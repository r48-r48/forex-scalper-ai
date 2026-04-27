"""Public interfaces for deterministic replay backtesting."""

from scalper_ai.backtesting.accounting import (
    apply_fill_to_cash,
    apply_fill_to_position,
    calculate_drawdown,
    calculate_equity,
    mark_position,
    simulate_market_fill,
)
from scalper_ai.backtesting.config import BacktestConfig
from scalper_ai.backtesting.engine import (
    BacktestEvent,
    BacktestMetrics,
    BacktestResult,
    BacktestState,
    TargetPositionStrategy,
    run_backtest,
)

__all__ = [
    "BacktestConfig",
    "BacktestEvent",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestState",
    "TargetPositionStrategy",
    "apply_fill_to_cash",
    "apply_fill_to_position",
    "calculate_drawdown",
    "calculate_equity",
    "mark_position",
    "run_backtest",
    "simulate_market_fill",
]

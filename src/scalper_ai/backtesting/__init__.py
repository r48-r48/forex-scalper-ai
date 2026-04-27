"""Public interfaces for deterministic replay backtesting."""

from scalper_ai.backtesting.accounting import (
    apply_fill_to_cash,
    apply_fill_to_position,
    calculate_drawdown,
    calculate_equity,
    mark_position,
    simulate_market_fill,
)
from scalper_ai.backtesting.baselines import (
    BaselineRiskConfig,
    BaselineStrategySpec,
    OfiImbalanceConfig,
    OfiImbalanceStrategy,
    SpreadMeanReversionConfig,
    SpreadMeanReversionStrategy,
    VolatilityBreakoutConfig,
    VolatilityBreakoutStrategy,
    build_default_baseline_specs,
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
from scalper_ai.backtesting.execution_simulator import (
    ExecutionBacktestResult,
    ExecutionQualityMetrics,
    ExecutionSimulatorConfig,
    SimulatedExecutionOrder,
    SimulatedOrderStatus,
    run_execution_aware_backtest,
)

__all__ = [
    "BacktestConfig",
    "BacktestEvent",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestState",
    "BaselineRiskConfig",
    "BaselineStrategySpec",
    "ExecutionBacktestResult",
    "ExecutionQualityMetrics",
    "ExecutionSimulatorConfig",
    "OfiImbalanceConfig",
    "OfiImbalanceStrategy",
    "SimulatedExecutionOrder",
    "SimulatedOrderStatus",
    "SpreadMeanReversionConfig",
    "SpreadMeanReversionStrategy",
    "TargetPositionStrategy",
    "VolatilityBreakoutConfig",
    "VolatilityBreakoutStrategy",
    "apply_fill_to_cash",
    "apply_fill_to_position",
    "build_default_baseline_specs",
    "calculate_drawdown",
    "calculate_equity",
    "mark_position",
    "run_backtest",
    "run_execution_aware_backtest",
    "simulate_market_fill",
]

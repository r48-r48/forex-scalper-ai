"""Baseline strategy reporting helpers for backtest and walk-forward validation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace

import pandas as pd

from scalper_ai.backtesting import (
    BacktestConfig,
    BacktestResult,
    TargetPositionStrategy,
    run_backtest,
)
from scalper_ai.backtesting.baselines import BaselineStrategySpec, build_default_baseline_specs
from scalper_ai.data.datasets import SupervisedDataset
from scalper_ai.data.splits import WalkForwardConfig, WalkForwardSplit
from scalper_ai.validation.walk_forward import (
    WalkForwardStrategyFactory,
    WalkForwardValidationResult,
    run_walk_forward_validation,
)


@dataclass(frozen=True)
class BaselineBacktestRun:
    """One baseline strategy backtest run."""

    strategy_name: str
    description: str
    backtest_config: BacktestConfig
    result: BacktestResult


@dataclass(frozen=True)
class BaselineSuiteResult:
    """Backtest outputs and summary frame for a baseline suite."""

    runs: tuple[BaselineBacktestRun, ...]
    summary: pd.DataFrame


@dataclass(frozen=True)
class BaselineSensitivityScenario:
    """Explicit cost and risk-limit scenario for baseline sensitivity analysis."""

    name: str
    spread_bps: float
    slippage_bps: float
    commission_bps: float
    max_abs_position: float

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Sensitivity scenario name must be non-empty.")
        if self.spread_bps < 0:
            raise ValueError("spread_bps must be non-negative.")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative.")
        if self.commission_bps < 0:
            raise ValueError("commission_bps must be non-negative.")
        if self.max_abs_position <= 0:
            raise ValueError("max_abs_position must be greater than zero.")


@dataclass(frozen=True)
class BaselineWalkForwardRun:
    """One baseline strategy walk-forward validation run."""

    strategy_name: str
    description: str
    result: WalkForwardValidationResult


@dataclass(frozen=True)
class BaselineWalkForwardSuiteResult:
    """Walk-forward outputs and report frames for a baseline suite."""

    runs: tuple[BaselineWalkForwardRun, ...]
    fold_metrics: pd.DataFrame
    summary: pd.DataFrame


def run_baseline_suite(
    frame: pd.DataFrame,
    *,
    baseline_specs: Sequence[BaselineStrategySpec] | None = None,
    backtest_config: BacktestConfig | None = None,
) -> BaselineSuiteResult:
    """Run a set of baseline strategies on one replay frame."""

    resolved_specs = tuple(baseline_specs or build_default_baseline_specs())
    if not resolved_specs:
        raise ValueError("At least one baseline strategy spec is required.")

    resolved_config = backtest_config or BacktestConfig()
    runs: list[BaselineBacktestRun] = []
    rows: list[dict[str, object]] = []
    for spec in resolved_specs:
        result = run_backtest(frame, spec.build(), config=resolved_config)
        run = BaselineBacktestRun(
            strategy_name=spec.name,
            description=spec.description,
            backtest_config=resolved_config,
            result=result,
        )
        runs.append(run)
        rows.append(_backtest_summary_row(run))

    return BaselineSuiteResult(
        runs=tuple(runs),
        summary=pd.DataFrame.from_records(rows),
    )


def run_default_baseline_sensitivity(
    frame: pd.DataFrame,
    *,
    scenarios: Sequence[BaselineSensitivityScenario] | None = None,
    base_backtest_config: BacktestConfig | None = None,
    max_spread_bps: float | None = 2.0,
) -> pd.DataFrame:
    """Run default baselines across explicit cost and position-limit scenarios."""

    resolved_scenarios = tuple(scenarios or default_baseline_sensitivity_scenarios())
    if not resolved_scenarios:
        raise ValueError("At least one sensitivity scenario is required.")

    base_config = base_backtest_config or BacktestConfig()
    frames: list[pd.DataFrame] = []
    for scenario in resolved_scenarios:
        scenario_config = replace(
            base_config,
            spread_bps=scenario.spread_bps,
            slippage_bps=scenario.slippage_bps,
            commission_bps=scenario.commission_bps,
        )
        specs = build_default_baseline_specs(
            max_abs_position=scenario.max_abs_position,
            max_spread_bps=max_spread_bps,
        )
        suite = run_baseline_suite(
            frame,
            baseline_specs=specs,
            backtest_config=scenario_config,
        )
        scenario_frame = suite.summary.copy()
        scenario_frame.insert(0, "scenario_name", scenario.name)
        scenario_frame["scenario_max_abs_position"] = scenario.max_abs_position
        frames.append(scenario_frame)

    return pd.concat(frames, ignore_index=True)


def run_baseline_walk_forward_suite(
    dataset: SupervisedDataset,
    *,
    baseline_specs: Sequence[BaselineStrategySpec] | None = None,
    walk_forward_config: WalkForwardConfig,
    backtest_config: BacktestConfig | None = None,
) -> BaselineWalkForwardSuiteResult:
    """Evaluate baseline strategies over ordered out-of-sample walk-forward folds."""

    resolved_specs = tuple(baseline_specs or build_default_baseline_specs())
    if not resolved_specs:
        raise ValueError("At least one baseline strategy spec is required.")

    runs: list[BaselineWalkForwardRun] = []
    fold_frames: list[pd.DataFrame] = []
    summary_rows: list[dict[str, object]] = []
    for spec in resolved_specs:
        result = run_walk_forward_validation(
            dataset,
            _walk_forward_factory(spec),
            walk_forward_config=walk_forward_config,
            backtest_config=backtest_config,
        )
        runs.append(
            BaselineWalkForwardRun(
                strategy_name=spec.name,
                description=spec.description,
                result=result,
            )
        )
        fold_frame = result.fold_metrics.copy()
        fold_frame.insert(0, "strategy_name", spec.name)
        fold_frame.insert(1, "description", spec.description)
        fold_frames.append(fold_frame)

        summary_row = asdict(result.summary)
        summary_row["strategy_name"] = spec.name
        summary_row["description"] = spec.description
        summary_rows.append(summary_row)

    return BaselineWalkForwardSuiteResult(
        runs=tuple(runs),
        fold_metrics=pd.concat(fold_frames, ignore_index=True),
        summary=pd.DataFrame.from_records(summary_rows),
    )


def default_baseline_sensitivity_scenarios() -> tuple[BaselineSensitivityScenario, ...]:
    """Return explicit cost/risk scenarios used by the default baseline report."""

    return (
        BaselineSensitivityScenario(
            name="low_cost_full_risk",
            spread_bps=0.5,
            slippage_bps=0.2,
            commission_bps=0.0,
            max_abs_position=1.0,
        ),
        BaselineSensitivityScenario(
            name="high_cost_full_risk",
            spread_bps=2.0,
            slippage_bps=1.0,
            commission_bps=0.2,
            max_abs_position=1.0,
        ),
        BaselineSensitivityScenario(
            name="low_cost_half_risk",
            spread_bps=0.5,
            slippage_bps=0.2,
            commission_bps=0.0,
            max_abs_position=0.5,
        ),
    )


def _walk_forward_factory(spec: BaselineStrategySpec) -> WalkForwardStrategyFactory:
    def factory(
        *,
        train: SupervisedDataset,
        validation: SupervisedDataset,
        test: SupervisedDataset,
        split: WalkForwardSplit,
    ) -> TargetPositionStrategy:
        del train, validation, test, split
        return spec.build()

    return factory


def _backtest_summary_row(run: BaselineBacktestRun) -> dict[str, object]:
    row: dict[str, object] = {
        "strategy_name": run.strategy_name,
        "description": run.description,
        "spread_bps": run.backtest_config.spread_bps,
        "slippage_bps": run.backtest_config.slippage_bps,
        "commission_bps": run.backtest_config.commission_bps,
        "initial_cash": run.backtest_config.initial_cash,
    }
    row.update(asdict(run.result.metrics))
    return row

"""Unit tests for baseline suite reporting helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scalper_ai.backtesting import BacktestConfig, build_default_baseline_specs
from scalper_ai.validation import (
    BaselineSensitivityScenario,
    run_baseline_suite,
    run_default_baseline_sensitivity,
)


def test_run_baseline_suite_returns_one_summary_row_per_strategy() -> None:
    result = run_baseline_suite(
        _market_frame(),
        baseline_specs=build_default_baseline_specs(max_abs_position=2.0),
        backtest_config=BacktestConfig(initial_cash=100_000.0, spread_bps=0.5),
    )

    assert len(result.runs) == 3
    assert result.summary["strategy_name"].tolist() == [
        "spread_mean_reversion",
        "ofi_imbalance",
        "volatility_breakout",
    ]
    assert result.summary["spread_bps"].tolist() == pytest.approx([0.5, 0.5, 0.5])
    assert set(result.summary.columns).issuperset(
        {
            "total_pnl",
            "final_equity",
            "max_drawdown",
            "trade_count",
            "turnover_quote",
        }
    )
    assert result.summary["trade_count"].sum() > 0


def test_run_default_baseline_sensitivity_reports_explicit_costs_and_risk_limits() -> None:
    scenarios = (
        BaselineSensitivityScenario(
            name="cheap_full",
            spread_bps=0.2,
            slippage_bps=0.1,
            commission_bps=0.0,
            max_abs_position=1.0,
        ),
        BaselineSensitivityScenario(
            name="expensive_half",
            spread_bps=2.0,
            slippage_bps=1.0,
            commission_bps=0.2,
            max_abs_position=0.5,
        ),
    )

    frame = run_default_baseline_sensitivity(
        _market_frame(),
        scenarios=scenarios,
        base_backtest_config=BacktestConfig(initial_cash=50_000.0),
    )

    assert len(frame) == 6
    assert sorted(frame["scenario_name"].unique().tolist()) == ["cheap_full", "expensive_half"]
    assert frame.groupby("scenario_name")["strategy_name"].nunique().to_dict() == {
        "cheap_full": 3,
        "expensive_half": 3,
    }
    expensive = frame[frame["scenario_name"] == "expensive_half"]
    assert expensive["spread_bps"].unique().tolist() == pytest.approx([2.0])
    assert expensive["slippage_bps"].unique().tolist() == pytest.approx([1.0])
    assert expensive["commission_bps"].unique().tolist() == pytest.approx([0.2])
    assert expensive["scenario_max_abs_position"].unique().tolist() == pytest.approx([0.5])


def _market_frame() -> pd.DataFrame:
    base_time = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
    records: list[dict[str, object]] = []
    returns = [0.0, -0.002, 0.002, 0.003, -0.003, 0.00001]
    ofi_values = [0.0, 2.0, -2.0, 3.0, -3.0, 0.0]
    for index, mid_return in enumerate(returns):
        timestamp = base_time + timedelta(minutes=index)
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp,
                "available_timestamp": timestamp,
                "mid_price": 100.0 + index,
                "mid_return": mid_return,
                "spread_bps": 0.5,
                "ofi": ofi_values[index],
                "mlofi_total": ofi_values[index],
                "realized_volatility": 0.001,
            }
        )
    return pd.DataFrame.from_records(records)

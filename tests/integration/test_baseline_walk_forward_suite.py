"""Integration tests for baseline walk-forward reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from scalper_ai.backtesting import BacktestConfig, build_default_baseline_specs
from scalper_ai.data.datasets import DatasetConfig, build_supervised_dataset
from scalper_ai.data.splits import WalkForwardConfig
from scalper_ai.validation import run_baseline_walk_forward_suite


def test_run_baseline_walk_forward_suite_reports_each_baseline_per_fold() -> None:
    dataset = build_supervised_dataset(
        feature_frame=_feature_frame(row_count=18),
        config=DatasetConfig(
            history_length=2,
            horizon=1,
            target_column="mid_return",
        ),
    )

    result = run_baseline_walk_forward_suite(
        dataset,
        baseline_specs=build_default_baseline_specs(max_abs_position=1.0),
        walk_forward_config=WalkForwardConfig(
            train_size=4,
            validation_size=2,
            test_size=2,
            embargo_size=1,
            step_size=2,
        ),
        backtest_config=BacktestConfig(initial_cash=100_000.0, price_column="mid_price"),
    )

    assert len(result.runs) == 3
    assert result.summary["strategy_name"].tolist() == [
        "spread_mean_reversion",
        "ofi_imbalance",
        "volatility_breakout",
    ]
    assert result.summary["fold_count"].tolist() == [4, 4, 4]
    assert set(result.fold_metrics["strategy_name"].unique()) == {
        "spread_mean_reversion",
        "ofi_imbalance",
        "volatility_breakout",
    }
    assert result.fold_metrics.groupby("strategy_name")["split_index"].nunique().to_dict() == {
        "spread_mean_reversion": 4,
        "ofi_imbalance": 4,
        "volatility_breakout": 4,
    }
    assert result.summary["total_trade_count"].sum() > 0
    assert result.summary["total_pnl"].map(float).notna().all()


def _feature_frame(*, row_count: int) -> pd.DataFrame:
    base_time = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
    records: list[dict[str, object]] = []
    for index in range(row_count):
        timestamp = base_time + timedelta(minutes=index)
        direction = 1.0 if index % 4 < 2 else -1.0
        mid_return = direction * 0.0015
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp,
                "available_timestamp": timestamp,
                "feature_set": "microstructure",
                "feature_version": "v1",
                "mid_price": 100.0 + (index * 0.1),
                "mid_return": mid_return,
                "spread_bps": 0.5,
                "ofi": direction * 2.0,
                "mlofi_total": direction * 2.0,
                "realized_volatility": 0.0005,
            }
        )
    return pd.DataFrame.from_records(records)

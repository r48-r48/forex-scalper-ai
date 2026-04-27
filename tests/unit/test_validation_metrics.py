"""Unit tests for walk-forward validation metrics and helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scalper_ai.backtesting import BacktestConfig
from scalper_ai.data.datasets import SupervisedDataset
from scalper_ai.data.splits import WalkForwardConfig
from scalper_ai.validation import (
    FoldValidationMetrics,
    run_walk_forward_validation,
    summarize_fold_metrics,
    supervised_partition_to_backtest_frame,
)


def test_summarize_fold_metrics_aggregates_pnl_drawdown_and_activity() -> None:
    summary = summarize_fold_metrics(
        [
            FoldValidationMetrics(
                split_index=0,
                train_size=4,
                validation_size=2,
                test_size=2,
                train_end_timestamp=_timestamp(4),
                validation_end_timestamp=_timestamp(6),
                test_end_timestamp=_timestamp(8),
                total_pnl=10.0,
                final_equity=100_010.0,
                max_drawdown=0.02,
                trade_count=2,
                turnover_quote=300.0,
            ),
            FoldValidationMetrics(
                split_index=1,
                train_size=4,
                validation_size=2,
                test_size=2,
                train_end_timestamp=_timestamp(6),
                validation_end_timestamp=_timestamp(8),
                test_end_timestamp=_timestamp(10),
                total_pnl=-5.0,
                final_equity=99_995.0,
                max_drawdown=0.05,
                trade_count=1,
                turnover_quote=120.0,
            ),
            FoldValidationMetrics(
                split_index=2,
                train_size=4,
                validation_size=2,
                test_size=2,
                train_end_timestamp=_timestamp(8),
                validation_end_timestamp=_timestamp(10),
                test_end_timestamp=_timestamp(12),
                total_pnl=0.0,
                final_equity=100_000.0,
                max_drawdown=0.01,
                trade_count=0,
                turnover_quote=0.0,
            ),
        ]
    )

    assert summary.fold_count == 3
    assert summary.total_pnl == pytest.approx(5.0)
    assert summary.mean_pnl == pytest.approx(5.0 / 3.0)
    assert summary.median_pnl == pytest.approx(0.0)
    assert summary.pnl_std == pytest.approx(6.2360956446)
    assert summary.best_fold_pnl == pytest.approx(10.0)
    assert summary.worst_fold_pnl == pytest.approx(-5.0)
    assert summary.profitable_fold_ratio == pytest.approx(1.0 / 3.0)
    assert summary.mean_final_equity == pytest.approx(100_001.6666666667)
    assert summary.mean_max_drawdown == pytest.approx(0.0266666667)
    assert summary.worst_max_drawdown == pytest.approx(0.05)
    assert summary.total_trade_count == 3
    assert summary.mean_trade_count == pytest.approx(1.0)
    assert summary.total_turnover_quote == pytest.approx(420.0)
    assert summary.mean_turnover_quote == pytest.approx(140.0)


def test_supervised_partition_to_backtest_frame_uses_current_lag_only() -> None:
    partition = SupervisedDataset(
        features=pd.DataFrame(
            {
                "lag_000__mid_price": [100.0, 101.0],
                "lag_000__signal": [1.0, -1.0],
                "lag_001__mid_price": [99.0, 100.0],
            }
        ),
        targets=pd.Series([0.1, -0.1], name="target"),
        metadata=pd.DataFrame(
            {
                "symbol": ["EURUSD", "EURUSD"],
                "event_timestamp": [_timestamp(0), _timestamp(1)],
                "available_timestamp": [_timestamp(0), _timestamp(1)],
                "feature_set": ["microstructure", "microstructure"],
                "feature_version": ["v1", "v1"],
            }
        ),
    )

    frame = supervised_partition_to_backtest_frame(partition)

    assert frame.columns.tolist() == [
        "symbol",
        "event_timestamp",
        "available_timestamp",
        "mid_price",
        "signal",
    ]
    assert frame["mid_price"].tolist() == pytest.approx([100.0, 101.0])
    assert frame["signal"].tolist() == pytest.approx([1.0, -1.0])


def test_run_walk_forward_validation_rejects_empty_split_set() -> None:
    dataset = SupervisedDataset(
        features=pd.DataFrame(
            {
                "lag_000__mid_price": [100.0, 101.0],
                "lag_000__signal": [1.0, -1.0],
            }
        ),
        targets=pd.Series([0.1, -0.1], name="target"),
        metadata=pd.DataFrame(
            {
                "symbol": ["EURUSD", "EURUSD"],
                "event_timestamp": [_timestamp(0), _timestamp(1)],
                "available_timestamp": [_timestamp(0), _timestamp(1)],
                "feature_set": ["microstructure", "microstructure"],
                "feature_version": ["v1", "v1"],
            }
        ),
    )

    with pytest.raises(ValueError, match="No walk-forward splits"):
        run_walk_forward_validation(
            dataset,
            _strategy_factory,
            walk_forward_config=WalkForwardConfig(
                train_size=2,
                validation_size=1,
                test_size=1,
            ),
            backtest_config=BacktestConfig(),
        )


def _strategy_factory(*, train, validation, test, split):
    del train, validation, test, split

    def strategy(event, state) -> float:
        del state
        return float(event.row_payload["signal"])

    return strategy


def _timestamp(minutes: int) -> datetime:
    return datetime(2026, 3, 28, 9, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)

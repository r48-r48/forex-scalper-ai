"""Integration tests for walk-forward validation over backtest folds."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from scalper_ai.backtesting import BacktestConfig
from scalper_ai.data.datasets import DatasetConfig, build_supervised_dataset
from scalper_ai.data.splits import WalkForwardConfig
from scalper_ai.validation import (
    run_walk_forward_validation,
    supervised_partition_to_backtest_frame,
)


def test_run_walk_forward_validation_evaluates_test_folds_and_aggregates_metrics() -> None:
    dataset = build_supervised_dataset(
        feature_frame=_feature_frame(row_count=16),
        config=DatasetConfig(
            history_length=2,
            horizon=1,
            target_column="mid_return",
        ),
    )

    result = run_walk_forward_validation(
        dataset,
        _strategy_factory,
        walk_forward_config=WalkForwardConfig(
            train_size=4,
            validation_size=2,
            test_size=2,
            embargo_size=1,
            step_size=2,
        ),
        backtest_config=BacktestConfig(
            initial_cash=100_000.0,
            price_column="mid_price",
        ),
    )

    assert len(result.folds) == 3
    assert result.fold_metrics["split_index"].tolist() == [0, 1, 2]
    assert result.fold_metrics["test_size"].tolist() == [2, 2, 2]
    assert all(len(fold.backtest_result.equity_curve) == 2 for fold in result.folds)
    assert result.summary.fold_count == 3
    assert result.summary.total_pnl == pytest.approx(float(result.fold_metrics["total_pnl"].sum()))
    assert result.summary.total_trade_count == int(result.fold_metrics["trade_count"].sum())
    assert result.summary.profitable_fold_ratio == pytest.approx(
        float((result.fold_metrics["total_pnl"] > 0).mean())
    )

    first_fold = result.folds[0]
    assert (
        first_fold.partitions.train.metadata["available_timestamp"].max()
        < first_fold.partitions.validation.metadata["available_timestamp"].min()
    )
    assert (
        first_fold.partitions.validation.metadata["available_timestamp"].max()
        < first_fold.partitions.test.metadata["available_timestamp"].min()
    )
    assert first_fold.backtest_result.equity_curve["timestamp"].tolist() == list(
        first_fold.partitions.test.metadata["available_timestamp"]
    )


def _strategy_factory(*, train, validation, test, split):
    del train, test, split
    validation_frame = supervised_partition_to_backtest_frame(validation)
    threshold = float(validation_frame["signal"].abs().median()) * 0.5
    return _SignalThresholdStrategy(threshold=threshold)


class _SignalThresholdStrategy:
    strategy_id = "signal-threshold"

    def __init__(self, *, threshold: float) -> None:
        self._threshold = threshold

    def __call__(self, event, state) -> float:
        del state
        signal = float(event.row_payload["signal"])
        if signal > self._threshold:
            return 1.0
        if signal < -self._threshold:
            return -1.0
        return 0.0


def _feature_frame(*, row_count: int) -> pd.DataFrame:
    base_time = datetime(2026, 3, 28, 9, 0, 0, tzinfo=UTC)
    records: list[dict[str, object]] = []
    for index in range(row_count):
        timestamp = base_time + timedelta(minutes=index)
        signal = 0.8 if index % 4 < 2 else -0.8
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp,
                "available_timestamp": timestamp,
                "feature_set": "microstructure",
                "feature_version": "v1",
                "mid_price": 100.0 + index,
                "signal": signal,
                "mid_return": signal * 0.001,
            }
        )
    return pd.DataFrame.from_records(records)

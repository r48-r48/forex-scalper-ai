"""Integration tests for supervised filter walk-forward evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from scalper_ai.data.datasets import DatasetConfig, build_supervised_dataset
from scalper_ai.data.splits import WalkForwardConfig
from scalper_ai.models import SupervisedBaselineFilterConfig
from scalper_ai.validation import run_supervised_filter_walk_forward


def test_supervised_filter_walk_forward_evaluates_only_out_of_sample_rows() -> None:
    dataset = build_supervised_dataset(
        feature_frame=_feature_frame(row_count=24),
        config=DatasetConfig(
            history_length=2,
            horizon=1,
            target_column="mid_return",
        ),
    )

    result = run_supervised_filter_walk_forward(
        dataset,
        walk_forward_config=WalkForwardConfig(
            train_size=6,
            validation_size=2,
            test_size=3,
            embargo_size=1,
            step_size=3,
        ),
        model_config=SupervisedBaselineFilterConfig(target_threshold=0.0001),
    )

    assert len(result.folds) == 4
    assert result.fold_metrics["split_index"].tolist() == [0, 1, 2, 3]
    assert result.summary.fold_count == 4
    assert result.summary.total_test_size == 12
    assert result.summary.total_evaluated_count == 12
    assert result.summary.mean_accuracy >= 0.75
    assert result.feature_importance.iloc[0]["feature"].endswith("__signal")

    first_fold = result.folds[0]
    assert (
        first_fold.partitions.train.metadata["available_timestamp"].max()
        < first_fold.partitions.test.metadata["available_timestamp"].min()
    )


def _feature_frame(*, row_count: int) -> pd.DataFrame:
    base_time = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
    signals = [1.0 if index % 4 < 2 else -1.0 for index in range(row_count)]
    records: list[dict[str, object]] = []
    for index, signal in enumerate(signals):
        timestamp = base_time + timedelta(minutes=index)
        previous_signal = signals[index - 1] if index > 0 else signal
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp,
                "available_timestamp": timestamp,
                "feature_set": "microstructure",
                "feature_version": "v1",
                "mid_price": 100.0 + (index * 0.1),
                "signal": signal,
                "spread_bps": 0.5,
                "mid_return": previous_signal * 0.002,
            }
        )
    return pd.DataFrame.from_records(records)

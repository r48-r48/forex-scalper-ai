"""Integration tests for walk-forward dataset splitting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from scalper_ai.data.datasets import DatasetConfig, build_supervised_dataset
from scalper_ai.data.splits import (
    WalkForwardConfig,
    generate_walk_forward_splits,
    materialize_walk_forward_split,
)


def test_walk_forward_splits_preserve_temporal_order_and_embargo() -> None:
    dataset = build_supervised_dataset(
        feature_frame=_make_feature_frame(row_count=15),
        config=DatasetConfig(
            history_length=2,
            horizon=1,
            target_column="mid_return",
        ),
    )

    splits = generate_walk_forward_splits(
        dataset,
        config=WalkForwardConfig(
            train_size=4,
            validation_size=2,
            test_size=2,
            embargo_size=1,
            step_size=2,
        ),
    )

    assert len(splits) == 2

    first = splits[0]
    partitions = materialize_walk_forward_split(dataset, first)

    assert len(partitions.train) == 4
    assert len(partitions.validation) == 2
    assert len(partitions.test) == 2
    assert (
        partitions.train.metadata["available_timestamp"].max()
        < partitions.validation.metadata["available_timestamp"].min()
    )
    assert (
        partitions.validation.metadata["available_timestamp"].max()
        < partitions.test.metadata["available_timestamp"].min()
    )


def _make_feature_frame(*, row_count: int) -> pd.DataFrame:
    base_time = datetime(2026, 3, 26, 9, 0, 0, tzinfo=timezone.utc)
    records: list[dict[str, object]] = []
    for index in range(row_count):
        timestamp = base_time + timedelta(minutes=index)
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp,
                "available_timestamp": timestamp,
                "feature_set": "microstructure",
                "feature_version": "v1",
                "mid_return": 0.001 * ((index % 3) - 1),
                "spread": 0.0002,
                "quote_intensity": 0.2 + index,
            }
        )
    return pd.DataFrame.from_records(records)

"""Unit tests for supervised dataset builders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scalper_ai.data.datasets import DatasetConfig, build_supervised_dataset


def test_build_supervised_dataset_creates_lagged_feature_rows_per_symbol() -> None:
    frame = _make_multi_symbol_frame()

    dataset = build_supervised_dataset(
        feature_frame=frame,
        config=DatasetConfig(
            history_length=2,
            horizon=1,
            stride=1,
            target_column="mid_return",
            target_aggregation="sum",
        ),
    )

    assert len(dataset) == 4
    assert dataset.features.iloc[0]["lag_000__spread"] == pytest.approx(0.0002)
    assert dataset.features.iloc[0]["lag_001__spread"] == pytest.approx(0.0001)
    assert dataset.metadata.iloc[0]["symbol"] == "EURUSD"
    assert dataset.metadata.iloc[2]["symbol"] == "GBPUSD"


def test_build_supervised_dataset_preserves_causal_target_alignment() -> None:
    frame = _make_single_symbol_frame(mid_returns=[0.0, 0.1, -0.05, 0.2])

    dataset = build_supervised_dataset(
        feature_frame=frame,
        config=DatasetConfig(
            history_length=2,
            horizon=2,
            target_column="mid_return",
            target_aggregation="sum",
        ),
    )

    assert len(dataset) == 1
    assert dataset.targets.iloc[0] == pytest.approx(0.15)
    assert dataset.metadata.iloc[0]["available_timestamp"] < dataset.metadata.iloc[0]["target_end_timestamp"]


def _make_single_symbol_frame(*, mid_returns: list[float]) -> pd.DataFrame:
    return _make_frame(symbol="EURUSD", mid_returns=mid_returns, spread_base=0.0001)


def _make_multi_symbol_frame() -> pd.DataFrame:
    eurusd = _make_frame(symbol="EURUSD", mid_returns=[0.0, 0.1, 0.2, -0.1], spread_base=0.0001)
    gbpusd = _make_frame(symbol="GBPUSD", mid_returns=[0.0, -0.05, 0.03, 0.02], spread_base=0.0003)
    return pd.concat([eurusd, gbpusd], ignore_index=True)


def _make_frame(*, symbol: str, mid_returns: list[float], spread_base: float) -> pd.DataFrame:
    base_time = datetime(2026, 3, 26, 9, 0, 0, tzinfo=timezone.utc)
    records: list[dict[str, object]] = []
    for index, mid_return in enumerate(mid_returns):
        timestamp = base_time + timedelta(seconds=index)
        records.append(
            {
                "symbol": symbol,
                "event_timestamp": timestamp,
                "available_timestamp": timestamp,
                "feature_set": "microstructure",
                "feature_version": "v1",
                "mid_return": mid_return,
                "spread": spread_base + index * 0.0001,
                "quote_intensity": 0.1 + index,
            }
        )
    return pd.DataFrame.from_records(records)

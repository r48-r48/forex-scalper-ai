"""Unit tests for leakage-safe future target generation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scalper_ai.data.labels import TARGET_COLUMN, TargetConfig, add_future_targets


def test_add_future_targets_builds_sum_of_future_mid_returns() -> None:
    frame = _make_feature_frame(mid_returns=[0.1, 0.2, -0.1, 0.05])

    targeted = add_future_targets(
        frame,
        config=TargetConfig(horizon=2, value_column="mid_return", aggregation="sum"),
    )

    assert targeted[TARGET_COLUMN].tolist()[:2] == pytest.approx([0.1, -0.05])
    assert pd.isna(targeted[TARGET_COLUMN].iloc[2])
    assert pd.isna(targeted[TARGET_COLUMN].iloc[3])


def test_add_future_targets_supports_classification_mode() -> None:
    frame = _make_feature_frame(mid_returns=[0.0, 0.03, -0.2, 0.01])

    targeted = add_future_targets(
        frame,
        config=TargetConfig(
            horizon=1,
            value_column="mid_return",
            aggregation="sum",
            mode="classification",
            classification_threshold=0.02,
        ),
    )

    assert targeted[TARGET_COLUMN].tolist()[:3] == pytest.approx([1.0, -1.0, 0.0])


def _make_feature_frame(*, mid_returns: list[float]) -> pd.DataFrame:
    base_time = datetime(2026, 3, 26, 9, 0, 0, tzinfo=timezone.utc)
    records: list[dict[str, object]] = []
    for index, mid_return in enumerate(mid_returns):
        timestamp = base_time + timedelta(seconds=index)
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp,
                "available_timestamp": timestamp,
                "feature_set": "microstructure",
                "feature_version": "v1",
                "mid_return": mid_return,
                "spread": 0.0002,
            }
        )
    return pd.DataFrame.from_records(records)

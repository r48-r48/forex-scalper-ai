"""Unit tests for the interpretable supervised baseline filter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scalper_ai.data.datasets import SupervisedDataset
from scalper_ai.models import (
    SupervisedBaselineFilterConfig,
    fit_supervised_baseline_filter,
    target_directions,
)


def test_supervised_baseline_filter_fits_interpretable_directional_weights() -> None:
    dataset = _dataset()

    model = fit_supervised_baseline_filter(dataset)
    predictions = model.predict_frame(dataset.features)
    importance = model.feature_importance()

    assert predictions.tolist() == [-1, -1, 1, 1]
    assert str(importance.iloc[0]["feature"]).endswith("__signal")
    lag_zero_weight = float(
        importance.loc[importance["feature"] == "lag_000__signal", "abs_weight"].iloc[0]
    )
    assert lag_zero_weight > 0.0
    assert importance.iloc[0]["abs_weight"] > 0.0
    scores = model.score_frame(dataset.features)
    assert scores.iloc[-1] > scores.iloc[0]


def test_supervised_baseline_filter_respects_target_and_score_thresholds() -> None:
    dataset = _dataset(targets=(-0.004, -0.002, 0.002, 0.004))

    model = fit_supervised_baseline_filter(
        dataset,
        config=SupervisedBaselineFilterConfig(target_threshold=0.001, score_threshold=0.5),
    )
    labels = target_directions(dataset.targets, threshold=0.001)
    predictions = model.predict_frame(dataset.features)

    assert labels.tolist() == [-1, -1, 1, 1]
    assert set(predictions.tolist()).issubset({-1, 0, 1})


def test_supervised_baseline_filter_requires_both_directional_classes() -> None:
    dataset = _dataset(targets=(0.001, 0.002, 0.003, 0.004))

    with pytest.raises(ValueError, match="both positive and negative"):
        fit_supervised_baseline_filter(dataset)


def _dataset(targets=(-0.003, -0.002, 0.002, 0.003)) -> SupervisedDataset:
    features = pd.DataFrame(
        {
            "lag_000__signal": [-2.0, -1.0, 1.0, 2.0],
            "lag_001__signal": [-1.5, -1.0, 1.0, 1.5],
            "lag_000__spread_bps": [0.5, 0.5, 0.5, 0.5],
            "lag_001__spread_bps": [0.5, 0.5, 0.5, 0.5],
        }
    )
    base_time = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
    metadata = pd.DataFrame(
        {
            "symbol": ["EURUSD"] * len(features),
            "event_timestamp": [
                base_time + timedelta(minutes=index) for index in range(len(features))
            ],
            "available_timestamp": [
                base_time + timedelta(minutes=index) for index in range(len(features))
            ],
            "feature_set": ["unit"] * len(features),
            "feature_version": ["v1"] * len(features),
        }
    )
    return SupervisedDataset(
        features=features,
        targets=pd.Series(targets, name="target"),
        metadata=metadata,
    )

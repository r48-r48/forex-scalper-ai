"""Leakage-safe target generation for supervised dataset builders."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from scalper_ai.features.schema import MID_RETURN_FEATURE

TargetAggregation = Literal["sum", "mean", "last"]
TargetMode = Literal["regression", "classification"]

TARGET_COLUMN = "target"
TARGET_END_TIMESTAMP_COLUMN = "target_end_timestamp"
TARGET_END_EVENT_TIMESTAMP_COLUMN = "target_end_event_timestamp"


@dataclass(frozen=True)
class TargetConfig:
    """Configuration for future target generation from feature frames."""

    horizon: int = 1
    value_column: str = MID_RETURN_FEATURE
    aggregation: TargetAggregation = "sum"
    mode: TargetMode = "regression"
    classification_threshold: float = 0.0

    def __post_init__(self) -> None:
        if self.horizon <= 0:
            raise ValueError("horizon must be greater than zero.")
        if not self.value_column.strip():
            raise ValueError("value_column must be non-empty.")
        if self.aggregation not in {"sum", "mean", "last"}:
            raise ValueError("aggregation must be one of: sum, mean, last.")
        if self.mode not in {"regression", "classification"}:
            raise ValueError("mode must be one of: regression, classification.")
        if self.classification_threshold < 0:
            raise ValueError("classification_threshold must be non-negative.")


def add_future_targets(
    feature_frame: pd.DataFrame,
    *,
    config: TargetConfig | None = None,
) -> pd.DataFrame:
    """Return a frame with leakage-safe future targets derived per symbol."""

    resolved_config = config or TargetConfig()
    _validate_required_columns(feature_frame, required=(resolved_config.value_column,))

    frame = _prepare_feature_frame(feature_frame)
    target_values: list[float] = []
    target_end_timestamps: list[object] = []
    target_end_event_timestamps: list[object] = []

    for _, group in frame.groupby("symbol", sort=False):
        values = group[resolved_config.value_column].astype(float).tolist()
        available_timestamps = group["available_timestamp"].tolist()
        event_timestamps = group["event_timestamp"].tolist()

        for index in range(len(group)):
            end_index = index + resolved_config.horizon
            if end_index >= len(group):
                target_values.append(math.nan)
                target_end_timestamps.append(pd.NaT)
                target_end_event_timestamps.append(pd.NaT)
                continue

            future_values = values[index + 1 : end_index + 1]
            target_value = _aggregate_future_values(
                future_values,
                aggregation=resolved_config.aggregation,
            )
            if resolved_config.mode == "classification":
                target_value = float(
                    _classify_target(
                        target_value,
                        threshold=resolved_config.classification_threshold,
                    )
                )
            target_values.append(target_value)
            target_end_timestamps.append(available_timestamps[end_index])
            target_end_event_timestamps.append(event_timestamps[end_index])

    frame[TARGET_COLUMN] = target_values
    frame[TARGET_END_TIMESTAMP_COLUMN] = target_end_timestamps
    frame[TARGET_END_EVENT_TIMESTAMP_COLUMN] = target_end_event_timestamps
    return frame


def _prepare_feature_frame(feature_frame: pd.DataFrame) -> pd.DataFrame:
    _validate_required_columns(
        feature_frame,
        required=("symbol", "event_timestamp", "available_timestamp"),
    )
    frame = feature_frame.copy()
    frame.sort_values(
        by=["symbol", "available_timestamp", "event_timestamp"],
        inplace=True,
        kind="stable",
    )
    frame.reset_index(drop=True, inplace=True)
    _validate_timestamp_column(frame["event_timestamp"], name="event_timestamp")
    _validate_timestamp_column(frame["available_timestamp"], name="available_timestamp")
    return frame


def _aggregate_future_values(values: list[float], *, aggregation: TargetAggregation) -> float:
    if aggregation == "sum":
        return float(sum(values))
    if aggregation == "mean":
        return float(sum(values) / len(values))
    if aggregation == "last":
        return float(values[-1])
    raise ValueError(f"Unsupported target aggregation: {aggregation}")


def _classify_target(value: float, *, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def _validate_required_columns(feature_frame: pd.DataFrame, *, required: tuple[str, ...]) -> None:
    missing = [column for column in required if column not in feature_frame.columns]
    if missing:
        raise ValueError(f"Feature frame is missing required columns: {', '.join(missing)}")


def _validate_timestamp_column(series: pd.Series, *, name: str) -> None:
    for value in series:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            raise ValueError(f"{name} must contain timezone-aware timestamps.")

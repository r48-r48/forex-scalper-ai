"""Supervised dataset builders from feature snapshots or flat feature frames."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from scalper_ai.data.labels import (
    TARGET_COLUMN,
    TARGET_END_EVENT_TIMESTAMP_COLUMN,
    TARGET_END_TIMESTAMP_COLUMN,
    TargetConfig,
    add_future_targets,
)
from scalper_ai.domain import FeatureSnapshot

METADATA_COLUMNS = (
    "symbol",
    "event_timestamp",
    "available_timestamp",
    "feature_set",
    "feature_version",
)


@dataclass(frozen=True)
class DatasetConfig:
    """Configuration for leakage-safe supervised dataset construction."""

    history_length: int = 32
    horizon: int = 1
    stride: int = 1
    target_column: str = "mid_return"
    target_aggregation: str = "sum"
    target_mode: str = "regression"
    classification_threshold: float = 0.0

    def __post_init__(self) -> None:
        if self.history_length <= 0:
            raise ValueError("history_length must be greater than zero.")
        if self.horizon <= 0:
            raise ValueError("horizon must be greater than zero.")
        if self.stride <= 0:
            raise ValueError("stride must be greater than zero.")
        if not self.target_column.strip():
            raise ValueError("target_column must be non-empty.")
        if self.target_aggregation not in {"sum", "mean", "last"}:
            raise ValueError("target_aggregation must be one of: sum, mean, last.")
        if self.target_mode not in {"regression", "classification"}:
            raise ValueError("target_mode must be one of: regression, classification.")
        if self.classification_threshold < 0:
            raise ValueError("classification_threshold must be non-negative.")

    def to_target_config(self) -> TargetConfig:
        """Return the target-generation config implied by this dataset config."""

        return TargetConfig(
            horizon=self.horizon,
            value_column=self.target_column,
            aggregation=self.target_aggregation,
            mode=self.target_mode,
            classification_threshold=self.classification_threshold,
        )


@dataclass(frozen=True)
class SupervisedDataset:
    """Model-ready supervised dataset with aligned metadata."""

    features: pd.DataFrame
    targets: pd.Series
    metadata: pd.DataFrame

    def __len__(self) -> int:
        return int(len(self.features))

    @property
    def feature_columns(self) -> tuple[str, ...]:
        """Return the stable ordered flat feature columns."""

        return tuple(str(column) for column in self.features.columns)

    def to_frame(self) -> pd.DataFrame:
        """Return a single flat frame with metadata, features, and target."""

        frame = self.metadata.reset_index(drop=True).copy()
        feature_frame = self.features.reset_index(drop=True)
        frame = pd.concat([frame, feature_frame], axis=1)
        frame[TARGET_COLUMN] = self.targets.reset_index(drop=True)
        return frame

    def take(self, rows: slice | Sequence[int]) -> SupervisedDataset:
        """Return a row subset while preserving metadata alignment."""

        return SupervisedDataset(
            features=self.features.iloc[rows].reset_index(drop=True),
            targets=self.targets.iloc[rows].reset_index(drop=True),
            metadata=self.metadata.iloc[rows].reset_index(drop=True),
        )

    def to_numpy(self) -> tuple[np.ndarray, np.ndarray]:
        """Return feature and target arrays for downstream model code."""

        return (
            self.features.to_numpy(dtype=np.float64, copy=True),
            self.targets.to_numpy(copy=True),
        )


def build_supervised_dataset(
    *,
    feature_frame: pd.DataFrame | None = None,
    snapshots: Sequence[FeatureSnapshot] = (),
    config: DatasetConfig | None = None,
) -> SupervisedDataset:
    """Build a leakage-safe supervised dataset from features ordered by availability."""

    resolved_config = config or DatasetConfig()
    frame = _coerce_feature_frame(feature_frame=feature_frame, snapshots=snapshots)
    targeted_frame = add_future_targets(frame, config=resolved_config.to_target_config())

    feature_columns = tuple(_infer_feature_columns(targeted_frame))
    rows_features: list[dict[str, float]] = []
    rows_targets: list[float] = []
    rows_metadata: list[dict[str, object]] = []

    for _, group in targeted_frame.groupby("symbol", sort=False):
        group = group.reset_index(drop=True)
        for anchor_index in range(
            resolved_config.history_length - 1,
            len(group),
            resolved_config.stride,
        ):
            target_value = group.at[anchor_index, TARGET_COLUMN]
            if pd.isna(target_value):
                continue

            feature_row: dict[str, float] = {}
            for lag in range(resolved_config.history_length):
                source_row = group.iloc[anchor_index - lag]
                for column in feature_columns:
                    lagged_name = _lagged_feature_name(
                        lag=lag,
                        feature_name=column,
                    )
                    feature_row[lagged_name] = float(source_row[column])

            anchor_row = group.iloc[anchor_index]
            rows_features.append(feature_row)
            rows_targets.append(float(target_value))
            rows_metadata.append(
                {
                    "symbol": anchor_row["symbol"],
                    "event_timestamp": anchor_row["event_timestamp"],
                    "available_timestamp": anchor_row["available_timestamp"],
                    "feature_set": anchor_row.get("feature_set", "unknown"),
                    "feature_version": anchor_row.get("feature_version", "unknown"),
                    TARGET_END_TIMESTAMP_COLUMN: anchor_row[TARGET_END_TIMESTAMP_COLUMN],
                    TARGET_END_EVENT_TIMESTAMP_COLUMN: anchor_row[
                        TARGET_END_EVENT_TIMESTAMP_COLUMN
                    ],
                }
            )

    features = pd.DataFrame.from_records(rows_features)
    targets = pd.Series(rows_targets, name=TARGET_COLUMN)
    metadata = pd.DataFrame.from_records(rows_metadata)
    return SupervisedDataset(features=features, targets=targets, metadata=metadata)


def feature_snapshots_to_frame(snapshots: Sequence[FeatureSnapshot]) -> pd.DataFrame:
    """Convert canonical feature snapshots into a flat feature frame."""

    records: list[dict[str, object]] = []
    for snapshot in snapshots:
        record: dict[str, object] = {
            "symbol": snapshot.symbol,
            "event_timestamp": snapshot.event_timestamp,
            "available_timestamp": snapshot.available_timestamp,
            "feature_set": snapshot.feature_set,
            "feature_version": snapshot.feature_version,
        }
        record.update(snapshot.values)
        records.append(record)
    return pd.DataFrame.from_records(records)


def write_supervised_dataset(
    dataset: SupervisedDataset,
    *,
    path: Path,
    compression: str = "zstd",
) -> Path:
    """Persist a flat supervised dataset frame to Parquet."""

    path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_frame().to_parquet(
        path,
        index=False,
        compression=None if compression == "none" else compression,
    )
    return path


def _coerce_feature_frame(
    *,
    feature_frame: pd.DataFrame | None,
    snapshots: Sequence[FeatureSnapshot],
) -> pd.DataFrame:
    if feature_frame is None:
        if not snapshots:
            raise ValueError("Either feature_frame or snapshots must be provided.")
        frame = feature_snapshots_to_frame(snapshots)
    else:
        frame = feature_frame.copy()

    required_columns = {"symbol", "event_timestamp", "available_timestamp"}
    missing = required_columns.difference(frame.columns)
    if missing:
        raise ValueError(f"Feature frame is missing required columns: {', '.join(sorted(missing))}")
    frame.sort_values(
        by=["symbol", "available_timestamp", "event_timestamp"],
        inplace=True,
        kind="stable",
    )
    frame.reset_index(drop=True, inplace=True)
    return frame


def _infer_feature_columns(feature_frame: pd.DataFrame) -> list[str]:
    excluded = set(METADATA_COLUMNS) | {
        TARGET_COLUMN,
        TARGET_END_TIMESTAMP_COLUMN,
        TARGET_END_EVENT_TIMESTAMP_COLUMN,
    }
    return [str(column) for column in feature_frame.columns if column not in excluded]


def _lagged_feature_name(*, lag: int, feature_name: str) -> str:
    return f"lag_{lag:03d}__{feature_name}"

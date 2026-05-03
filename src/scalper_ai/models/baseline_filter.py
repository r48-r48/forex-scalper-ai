"""Interpretable supervised baseline filter for directional signal research."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
import pandas as pd

from scalper_ai.data.datasets import SupervisedDataset

_DEFAULT_MIN_SCALE = 1e-12
_ZERO_TOLERANCE = 1e-12


@dataclass(frozen=True)
class SupervisedBaselineFilterConfig:
    """Configuration for the transparent centroid-difference filter."""

    target_threshold: float = 0.0
    score_threshold: float = 0.0
    min_scale: float = _DEFAULT_MIN_SCALE

    def __post_init__(self) -> None:
        if self.target_threshold < 0:
            raise ValueError("target_threshold must be non-negative.")
        if self.score_threshold < 0:
            raise ValueError("score_threshold must be non-negative.")
        if self.min_scale <= 0:
            raise ValueError("min_scale must be greater than zero.")


@dataclass(frozen=True)
class SupervisedBaselineFilterModel:
    """Fitted transparent linear filter with feature-level explanations."""

    feature_columns: tuple[str, ...]
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float
    score_threshold: float = 0.0

    def __post_init__(self) -> None:
        expected = len(self.feature_columns)
        if expected == 0:
            raise ValueError("feature_columns must not be empty.")
        if len(self.feature_means) != expected:
            raise ValueError("feature_means length must match feature_columns.")
        if len(self.feature_scales) != expected:
            raise ValueError("feature_scales length must match feature_columns.")
        if len(self.weights) != expected:
            raise ValueError("weights length must match feature_columns.")
        if any(scale <= _ZERO_TOLERANCE for scale in self.feature_scales):
            raise ValueError("feature_scales must be positive.")
        model_parameters = (*self.feature_means, *self.feature_scales, *self.weights, self.bias)
        if not _finite_sequence(model_parameters):
            raise ValueError("model parameters must be finite.")
        if self.score_threshold < 0:
            raise ValueError("score_threshold must be non-negative.")

    def score_frame(self, feature_frame: pd.DataFrame) -> pd.Series:
        """Return signed model scores for a feature frame."""

        values = _coerce_feature_matrix(feature_frame, feature_columns=self.feature_columns)
        means = np.asarray(self.feature_means, dtype=float)
        scales = np.asarray(self.feature_scales, dtype=float)
        weights = np.asarray(self.weights, dtype=float)
        standardized = (values - means) / scales
        scores = standardized @ weights + float(self.bias)
        return pd.Series(scores, index=feature_frame.index, name="score")

    def predict_frame(self, feature_frame: pd.DataFrame) -> pd.Series:
        """Return directional predictions {-1, 0, 1} from model scores."""

        scores = self.score_frame(feature_frame)
        predictions = np.zeros(len(scores), dtype=int)
        predictions[scores.to_numpy(dtype=float) > self.score_threshold] = 1
        predictions[scores.to_numpy(dtype=float) < -self.score_threshold] = -1
        return pd.Series(predictions, index=feature_frame.index, name="prediction")

    def feature_importance(self) -> pd.DataFrame:
        """Return transparent per-feature weights sorted by absolute weight."""

        frame = pd.DataFrame(
            {
                "feature": self.feature_columns,
                "weight": self.weights,
                "abs_weight": np.abs(np.asarray(self.weights, dtype=float)),
                "mean": self.feature_means,
                "scale": self.feature_scales,
            }
        )
        return frame.sort_values(
            "abs_weight",
            ascending=False,
            kind="stable",
        ).reset_index(drop=True)


def fit_supervised_baseline_filter(
    dataset: SupervisedDataset,
    *,
    config: SupervisedBaselineFilterConfig | None = None,
) -> SupervisedBaselineFilterModel:
    """Fit an interpretable directional filter using only the provided dataset rows."""

    if len(dataset) == 0:
        raise ValueError("dataset must contain at least one row.")

    resolved_config = config or SupervisedBaselineFilterConfig()
    feature_columns = dataset.feature_columns
    values = _coerce_feature_matrix(dataset.features, feature_columns=feature_columns)
    targets = _coerce_target_array(dataset.targets)
    if len(targets) != len(values):
        raise ValueError("targets length must match feature rows.")
    labels = target_directions(targets, threshold=resolved_config.target_threshold)

    directional_mask = labels != 0
    if not directional_mask.any():
        raise ValueError("dataset must contain at least one non-neutral target.")
    if not (labels == 1).any() or not (labels == -1).any():
        raise ValueError("dataset must contain both positive and negative directional targets.")

    means = values.mean(axis=0)
    scales = values.std(axis=0, ddof=0)
    scales = np.where(scales < resolved_config.min_scale, 1.0, scales)
    standardized = (values - means) / scales

    positive_centroid = standardized[labels == 1].mean(axis=0)
    negative_centroid = standardized[labels == -1].mean(axis=0)
    weights = positive_centroid - negative_centroid
    midpoint = 0.5 * (positive_centroid + negative_centroid)
    bias = -float(midpoint @ weights)

    return SupervisedBaselineFilterModel(
        feature_columns=feature_columns,
        feature_means=tuple(float(value) for value in means),
        feature_scales=tuple(float(value) for value in scales),
        weights=tuple(float(value) for value in weights),
        bias=bias,
        score_threshold=resolved_config.score_threshold,
    )


def target_directions(targets: pd.Series | np.ndarray, *, threshold: float = 0.0) -> np.ndarray:
    """Convert numeric targets to directional labels {-1, 0, 1}."""

    if threshold < 0:
        raise ValueError("threshold must be non-negative.")
    values = _coerce_target_array(targets)
    labels = np.zeros(len(values), dtype=int)
    labels[values > threshold] = 1
    labels[values < -threshold] = -1
    return labels


def _coerce_feature_matrix(
    feature_frame: pd.DataFrame,
    *,
    feature_columns: tuple[str, ...],
) -> np.ndarray:
    missing = [column for column in feature_columns if column not in feature_frame.columns]
    if missing:
        raise ValueError(f"Feature frame is missing required columns: {', '.join(missing)}")

    values = cast(
        np.ndarray,
        feature_frame.loc[:, list(feature_columns)].to_numpy(dtype=float, copy=True),
    )
    if values.ndim != 2 or values.shape[1] != len(feature_columns):
        raise ValueError("feature frame must be a two-dimensional matrix.")
    if not np.isfinite(values).all():
        raise ValueError("feature frame contains non-finite values.")
    return values


def _coerce_target_array(targets: pd.Series | np.ndarray) -> np.ndarray:
    values = np.asarray(targets, dtype=float).reshape(-1)
    if not np.isfinite(values).all():
        raise ValueError("targets contain non-finite values.")
    return values


def _finite_sequence(values: tuple[float, ...]) -> bool:
    return bool(np.isfinite(np.asarray(values, dtype=float)).all())

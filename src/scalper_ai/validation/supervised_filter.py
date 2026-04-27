"""Walk-forward evaluation for the interpretable supervised baseline filter."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from scalper_ai.data.datasets import SupervisedDataset
from scalper_ai.data.splits import (
    DatasetPartitions,
    WalkForwardConfig,
    WalkForwardSplit,
    generate_walk_forward_splits,
    materialize_walk_forward_split,
)
from scalper_ai.models import (
    SupervisedBaselineFilterConfig,
    SupervisedBaselineFilterModel,
    fit_supervised_baseline_filter,
    target_directions,
)


@dataclass(frozen=True)
class SupervisedFilterFoldMetrics:
    """Fold-level directional metrics for the transparent supervised filter."""

    split_index: int
    train_size: int
    validation_size: int
    test_size: int
    train_end_timestamp: object
    validation_end_timestamp: object
    test_end_timestamp: object
    evaluated_count: int
    accuracy: float
    coverage: float
    long_ratio: float
    short_ratio: float
    neutral_ratio: float


@dataclass(frozen=True)
class SupervisedFilterFoldResult:
    """One fitted fold with out-of-sample predictions and metrics."""

    split: WalkForwardSplit
    partitions: DatasetPartitions
    model: SupervisedBaselineFilterModel
    scores: pd.Series
    predictions: pd.Series
    labels: pd.Series
    metrics: SupervisedFilterFoldMetrics


@dataclass(frozen=True)
class SupervisedFilterSummary:
    """Aggregate directional metrics across walk-forward folds."""

    fold_count: int
    total_test_size: int
    total_evaluated_count: int
    mean_accuracy: float
    mean_coverage: float
    long_ratio: float
    short_ratio: float
    neutral_ratio: float


@dataclass(frozen=True)
class SupervisedFilterWalkForwardResult:
    """Materialized walk-forward report for the transparent supervised filter."""

    folds: tuple[SupervisedFilterFoldResult, ...]
    fold_metrics: pd.DataFrame
    summary: SupervisedFilterSummary
    feature_importance: pd.DataFrame


def run_supervised_filter_walk_forward(
    dataset: SupervisedDataset,
    *,
    walk_forward_config: WalkForwardConfig,
    model_config: SupervisedBaselineFilterConfig | None = None,
) -> SupervisedFilterWalkForwardResult:
    """Fit on train folds and evaluate directional predictions on test folds only."""

    splits = generate_walk_forward_splits(dataset, config=walk_forward_config)
    if not splits:
        raise ValueError(
            "No walk-forward splits were generated for the provided dataset and config."
        )

    resolved_model_config = model_config or SupervisedBaselineFilterConfig()
    folds: list[SupervisedFilterFoldResult] = []
    metrics: list[SupervisedFilterFoldMetrics] = []
    importance_frames: list[pd.DataFrame] = []

    for split in splits:
        partitions = materialize_walk_forward_split(dataset, split)
        model = fit_supervised_baseline_filter(partitions.train, config=resolved_model_config)
        scores = model.score_frame(partitions.test.features).reset_index(drop=True)
        predictions = model.predict_frame(partitions.test.features).reset_index(drop=True)
        labels = pd.Series(
            target_directions(
                partitions.test.targets,
                threshold=resolved_model_config.target_threshold,
            ),
            name="label",
        )
        fold_metrics = _build_fold_metrics(
            split=split,
            partitions=partitions,
            predictions=predictions,
            labels=labels,
        )
        importance_frame = model.feature_importance().copy()
        importance_frame.insert(0, "split_index", split.split_index)

        folds.append(
            SupervisedFilterFoldResult(
                split=split,
                partitions=partitions,
                model=model,
                scores=scores,
                predictions=predictions,
                labels=labels,
                metrics=fold_metrics,
            )
        )
        metrics.append(fold_metrics)
        importance_frames.append(importance_frame)

    fold_metrics_frame = pd.DataFrame.from_records(asdict(metric) for metric in metrics)
    return SupervisedFilterWalkForwardResult(
        folds=tuple(folds),
        fold_metrics=fold_metrics_frame,
        summary=_summarize_filter_metrics(metrics),
        feature_importance=_summarize_feature_importance(importance_frames),
    )


def _build_fold_metrics(
    *,
    split: WalkForwardSplit,
    partitions: DatasetPartitions,
    predictions: pd.Series,
    labels: pd.Series,
) -> SupervisedFilterFoldMetrics:
    prediction_values = predictions.to_numpy(dtype=int, copy=False)
    label_values = labels.to_numpy(dtype=int, copy=False)
    evaluated_mask = label_values != 0
    evaluated_count = int(evaluated_mask.sum())
    accuracy = (
        float((prediction_values[evaluated_mask] == label_values[evaluated_mask]).mean())
        if evaluated_count
        else 0.0
    )
    total_predictions = max(1, len(prediction_values))
    long_count = int((prediction_values == 1).sum())
    short_count = int((prediction_values == -1).sum())
    neutral_count = int((prediction_values == 0).sum())

    return SupervisedFilterFoldMetrics(
        split_index=split.split_index,
        train_size=len(partitions.train),
        validation_size=len(partitions.validation),
        test_size=len(partitions.test),
        train_end_timestamp=split.train_end_timestamp,
        validation_end_timestamp=split.validation_end_timestamp,
        test_end_timestamp=split.test_end_timestamp,
        evaluated_count=evaluated_count,
        accuracy=accuracy,
        coverage=float((long_count + short_count) / total_predictions),
        long_ratio=float(long_count / total_predictions),
        short_ratio=float(short_count / total_predictions),
        neutral_ratio=float(neutral_count / total_predictions),
    )


def _summarize_filter_metrics(
    metrics: list[SupervisedFilterFoldMetrics],
) -> SupervisedFilterSummary:
    total_test_size = int(sum(metric.test_size for metric in metrics))
    total_predictions = max(1, total_test_size)
    weighted_correct = float(
        sum(metric.accuracy * metric.evaluated_count for metric in metrics)
    )
    total_evaluated = int(sum(metric.evaluated_count for metric in metrics))
    mean_accuracy = weighted_correct / total_evaluated if total_evaluated else 0.0
    weighted_coverage = float(sum(metric.coverage * metric.test_size for metric in metrics))
    weighted_long = float(sum(metric.long_ratio * metric.test_size for metric in metrics))
    weighted_short = float(sum(metric.short_ratio * metric.test_size for metric in metrics))
    weighted_neutral = float(sum(metric.neutral_ratio * metric.test_size for metric in metrics))

    return SupervisedFilterSummary(
        fold_count=len(metrics),
        total_test_size=total_test_size,
        total_evaluated_count=total_evaluated,
        mean_accuracy=mean_accuracy,
        mean_coverage=weighted_coverage / total_predictions,
        long_ratio=weighted_long / total_predictions,
        short_ratio=weighted_short / total_predictions,
        neutral_ratio=weighted_neutral / total_predictions,
    )


def _summarize_feature_importance(importance_frames: list[pd.DataFrame]) -> pd.DataFrame:
    frame = pd.concat(importance_frames, ignore_index=True)
    summary = (
        frame.groupby("feature", sort=True)
        .agg(
            mean_weight=("weight", "mean"),
            mean_abs_weight=("abs_weight", "mean"),
            fold_count=("split_index", "nunique"),
        )
        .reset_index()
    )
    return summary.sort_values(
        "mean_abs_weight",
        ascending=False,
        kind="stable",
    ).reset_index(drop=True)

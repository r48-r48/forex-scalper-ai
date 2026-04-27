"""Walk-forward validation orchestration over dataset splits and backtest runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from scalper_ai.backtesting import BacktestConfig, BacktestResult, TargetPositionStrategy, run_backtest
from scalper_ai.data.datasets import SupervisedDataset
from scalper_ai.data.splits import (
    DatasetPartitions,
    WalkForwardConfig,
    WalkForwardSplit,
    generate_walk_forward_splits,
    materialize_walk_forward_split,
)
from scalper_ai.validation.metrics import (
    FoldValidationMetrics,
    RobustnessSummary,
    fold_metrics_to_frame,
    summarize_fold_metrics,
)

_CURRENT_FEATURE_PREFIX = "lag_000__"
_PARTITION_METADATA_COLUMNS = ("symbol", "event_timestamp", "available_timestamp")


class PartitionFrameBuilder(Protocol):
    """Transform a dataset partition into a replay frame for the backtester."""

    def __call__(self, partition: SupervisedDataset) -> pd.DataFrame:
        """Return a backtest-ready dataframe for one out-of-sample partition."""


class WalkForwardStrategyFactory(Protocol):
    """Build a target-position strategy for one walk-forward fold."""

    def __call__(
        self,
        *,
        train: SupervisedDataset,
        validation: SupervisedDataset,
        test: SupervisedDataset,
        split: WalkForwardSplit,
    ) -> TargetPositionStrategy:
        """Return a strategy to be evaluated on the test partition."""


@dataclass(frozen=True)
class WalkForwardValidationFold:
    """One materialized walk-forward validation fold."""

    split: WalkForwardSplit
    partitions: DatasetPartitions
    backtest_result: BacktestResult
    metrics: FoldValidationMetrics


@dataclass(frozen=True)
class WalkForwardValidationResult:
    """Materialized fold outputs and aggregate validation summary."""

    folds: tuple[WalkForwardValidationFold, ...]
    fold_metrics: pd.DataFrame
    summary: RobustnessSummary


def supervised_partition_to_backtest_frame(
    partition: SupervisedDataset,
    *,
    current_feature_prefix: str = _CURRENT_FEATURE_PREFIX,
) -> pd.DataFrame:
    """Convert a lagged supervised dataset partition into a current-state replay frame."""

    if not current_feature_prefix:
        raise ValueError("current_feature_prefix must be non-empty.")

    frame = partition.to_frame()
    lagged_columns = [column for column in frame.columns if str(column).startswith(current_feature_prefix)]
    if not lagged_columns:
        raise ValueError(f"No feature columns were found with prefix: {current_feature_prefix}")

    backtest_frame = frame.loc[:, list(_PARTITION_METADATA_COLUMNS)].copy()
    renamed_columns: dict[str, str] = {}
    for column in lagged_columns:
        column_name = str(column)
        stripped_name = column_name[len(current_feature_prefix) :]
        if not stripped_name:
            raise ValueError("Backtest frame columns must not collapse to an empty name.")
        if stripped_name in backtest_frame.columns or stripped_name in renamed_columns.values():
            raise ValueError(f"Backtest frame contains duplicate stripped feature name: {stripped_name}")
        renamed_columns[column_name] = stripped_name

    current_features = frame.loc[:, lagged_columns].copy()
    current_features.rename(columns=renamed_columns, inplace=True)
    for column in current_features.columns:
        current_features[column] = current_features[column].astype(float)

    return pd.concat([backtest_frame, current_features], axis=1)


def run_walk_forward_validation(
    dataset: SupervisedDataset,
    strategy_factory: WalkForwardStrategyFactory,
    *,
    walk_forward_config: WalkForwardConfig,
    backtest_config: BacktestConfig | None = None,
    frame_builder: PartitionFrameBuilder = supervised_partition_to_backtest_frame,
) -> WalkForwardValidationResult:
    """Evaluate a strategy factory on ordered out-of-sample test folds only."""

    splits = generate_walk_forward_splits(dataset, config=walk_forward_config)
    if not splits:
        raise ValueError("No walk-forward splits were generated for the provided dataset and config.")

    resolved_backtest_config = backtest_config or BacktestConfig()
    folds: list[WalkForwardValidationFold] = []
    fold_metrics: list[FoldValidationMetrics] = []

    for split in splits:
        partitions = materialize_walk_forward_split(dataset, split)
        strategy = strategy_factory(
            train=partitions.train,
            validation=partitions.validation,
            test=partitions.test,
            split=split,
        )
        test_frame = frame_builder(partitions.test)
        backtest_result = run_backtest(
            test_frame,
            strategy,
            config=resolved_backtest_config,
        )
        metrics = FoldValidationMetrics(
            split_index=split.split_index,
            train_size=len(partitions.train),
            validation_size=len(partitions.validation),
            test_size=len(partitions.test),
            train_end_timestamp=split.train_end_timestamp,
            validation_end_timestamp=split.validation_end_timestamp,
            test_end_timestamp=split.test_end_timestamp,
            total_pnl=backtest_result.metrics.total_pnl,
            final_equity=backtest_result.metrics.final_equity,
            max_drawdown=backtest_result.metrics.max_drawdown,
            trade_count=backtest_result.metrics.trade_count,
            turnover_quote=backtest_result.metrics.turnover_quote,
        )
        folds.append(
            WalkForwardValidationFold(
                split=split,
                partitions=partitions,
                backtest_result=backtest_result,
                metrics=metrics,
            )
        )
        fold_metrics.append(metrics)

    metrics_frame = fold_metrics_to_frame(fold_metrics)
    summary = summarize_fold_metrics(fold_metrics)
    return WalkForwardValidationResult(
        folds=tuple(folds),
        fold_metrics=metrics_frame,
        summary=summary,
    )

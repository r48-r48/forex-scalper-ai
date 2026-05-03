"""Walk-forward dataset split helpers for forecasting and RL research."""

from __future__ import annotations

from dataclasses import dataclass

from scalper_ai.data.datasets import SupervisedDataset


@dataclass(frozen=True)
class WalkForwardConfig:
    """Configuration for ordered walk-forward dataset splits."""

    train_size: int
    validation_size: int
    test_size: int
    step_size: int | None = None
    embargo_size: int = 0

    def __post_init__(self) -> None:
        if self.train_size <= 0:
            raise ValueError("train_size must be greater than zero.")
        if self.validation_size <= 0:
            raise ValueError("validation_size must be greater than zero.")
        if self.test_size <= 0:
            raise ValueError("test_size must be greater than zero.")
        if self.step_size is not None and self.step_size <= 0:
            raise ValueError("step_size must be greater than zero when provided.")
        if self.embargo_size < 0:
            raise ValueError("embargo_size must be non-negative.")


@dataclass(frozen=True)
class WalkForwardSplit:
    """One ordered train/validation/test split over a supervised dataset."""

    split_index: int
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int
    train_end_timestamp: object
    validation_end_timestamp: object
    test_end_timestamp: object


@dataclass(frozen=True)
class DatasetPartitions:
    """Materialized dataset partitions for one walk-forward split."""

    train: SupervisedDataset
    validation: SupervisedDataset
    test: SupervisedDataset


def generate_walk_forward_splits(
    dataset: SupervisedDataset,
    *,
    config: WalkForwardConfig,
) -> list[WalkForwardSplit]:
    """Generate ordered walk-forward splits using dataset availability timestamps."""

    ordered_metadata = dataset.metadata.sort_values(
        by=["available_timestamp", "event_timestamp", "symbol"],
        kind="stable",
    ).reset_index(drop=True)
    total_required = (
        config.train_size
        + config.validation_size
        + config.test_size
        + 2 * config.embargo_size
    )
    if len(ordered_metadata) < total_required:
        return []

    step_size = config.step_size if config.step_size is not None else config.test_size
    splits: list[WalkForwardSplit] = []
    split_index = 0

    for start in range(0, len(ordered_metadata) - total_required + 1, step_size):
        train_start = start
        train_end = train_start + config.train_size
        validation_start = train_end + config.embargo_size
        validation_end = validation_start + config.validation_size
        test_start = validation_end + config.embargo_size
        test_end = test_start + config.test_size

        split = WalkForwardSplit(
            split_index=split_index,
            train_start=train_start,
            train_end=train_end,
            validation_start=validation_start,
            validation_end=validation_end,
            test_start=test_start,
            test_end=test_end,
            train_end_timestamp=ordered_metadata.iloc[train_end - 1]["available_timestamp"],
            validation_end_timestamp=ordered_metadata.iloc[validation_end - 1][
                "available_timestamp"
            ],
            test_end_timestamp=ordered_metadata.iloc[test_end - 1]["available_timestamp"],
        )
        splits.append(split)
        split_index += 1

    return splits


def materialize_walk_forward_split(
    dataset: SupervisedDataset,
    split: WalkForwardSplit,
) -> DatasetPartitions:
    """Return train/validation/test dataset partitions for one split."""

    ordered = _reorder_dataset(dataset)
    return DatasetPartitions(
        train=ordered.take(slice(split.train_start, split.train_end)),
        validation=ordered.take(slice(split.validation_start, split.validation_end)),
        test=ordered.take(slice(split.test_start, split.test_end)),
    )


def _reorder_dataset(dataset: SupervisedDataset) -> SupervisedDataset:
    ordering = (
        dataset.metadata.sort_values(
            by=["available_timestamp", "event_timestamp", "symbol"],
            kind="stable",
        )
        .index.to_list()
    )
    return dataset.take(ordering)

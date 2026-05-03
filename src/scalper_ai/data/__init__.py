"""Data ingestion, preprocessing, dataset building, and raw persistence."""

from scalper_ai.data.bar_builders import (
    BaseBarBuilder,
    ImbalanceBarBuilder,
    TickBarBuilder,
    TimeBarBuilder,
    VolatilityBarBuilder,
    build_bars,
)
from scalper_ai.data.buffering import BufferedBatchWriter, EventBatcher
from scalper_ai.data.collector import IngestionRunStats, ReplayCollector
from scalper_ai.data.datasets import (
    DatasetConfig,
    SupervisedDataset,
    build_supervised_dataset,
    feature_snapshots_to_frame,
    write_supervised_dataset,
)
from scalper_ai.data.interfaces import BatchWriter, BookStreamSource, TickStreamSource
from scalper_ai.data.labels import TargetConfig, add_future_targets
from scalper_ai.data.mt5 import Mt5BookIngestionAdapter, Mt5TickIngestionAdapter
from scalper_ai.data.preprocessing import (
    fractional_diff_weights,
    fractional_differentiate,
    mid_price,
)
from scalper_ai.data.quality import (
    DataQualityIssue,
    DataQualityReport,
    DataQualitySeverity,
    TickDataQualityConfig,
    check_tick_duplicates,
    check_tick_price_quality,
    check_tick_temporal_ordering,
    check_tick_timestamp_quality,
    validate_tick_data,
)
from scalper_ai.data.raw_writer import RawParquetWriter
from scalper_ai.data.replay import ReplayBookSource, ReplayTickSource
from scalper_ai.data.splits import (
    DatasetPartitions,
    WalkForwardConfig,
    WalkForwardSplit,
    generate_walk_forward_splits,
    materialize_walk_forward_split,
)

__all__ = [
    "BatchWriter",
    "BaseBarBuilder",
    "BookStreamSource",
    "BufferedBatchWriter",
    "DataQualityIssue",
    "DataQualityReport",
    "DataQualitySeverity",
    "DatasetConfig",
    "DatasetPartitions",
    "EventBatcher",
    "ImbalanceBarBuilder",
    "IngestionRunStats",
    "Mt5BookIngestionAdapter",
    "Mt5TickIngestionAdapter",
    "RawParquetWriter",
    "ReplayBookSource",
    "ReplayCollector",
    "ReplayTickSource",
    "SupervisedDataset",
    "TargetConfig",
    "TickBarBuilder",
    "TickDataQualityConfig",
    "TickStreamSource",
    "TimeBarBuilder",
    "VolatilityBarBuilder",
    "WalkForwardConfig",
    "WalkForwardSplit",
    "add_future_targets",
    "build_supervised_dataset",
    "build_bars",
    "check_tick_duplicates",
    "check_tick_price_quality",
    "check_tick_temporal_ordering",
    "check_tick_timestamp_quality",
    "feature_snapshots_to_frame",
    "fractional_differentiate",
    "fractional_diff_weights",
    "generate_walk_forward_splits",
    "materialize_walk_forward_split",
    "mid_price",
    "validate_tick_data",
    "write_supervised_dataset",
]

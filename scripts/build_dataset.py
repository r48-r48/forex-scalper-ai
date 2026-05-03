"""Build leakage-safe supervised datasets from flat feature frames."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"
for import_path in (SCRIPT_ROOT, SRC_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from cli_utils import dataframe_records, load_frame, write_frame, write_json

from scalper_ai.data import DatasetConfig, build_supervised_dataset


def build_dataset_cli(
    *,
    input_path: Path,
    output_path: Path,
    summary_output_path: Path | None = None,
    history_length: int = 32,
    horizon: int = 1,
    stride: int = 1,
    target_column: str = "mid_return",
    target_aggregation: str = "sum",
    target_mode: str = "regression",
    classification_threshold: float = 0.0,
    compression: str = "zstd",
) -> dict[str, object]:
    """Build a supervised dataset and return a JSON-ready summary payload."""

    feature_frame = load_frame(input_path)
    config = DatasetConfig(
        history_length=history_length,
        horizon=horizon,
        stride=stride,
        target_column=target_column,
        target_aggregation=target_aggregation,
        target_mode=target_mode,
        classification_threshold=classification_threshold,
    )
    dataset = build_supervised_dataset(feature_frame=feature_frame, config=config)
    output_frame = dataset.to_frame()
    write_frame(output_frame, output_path, compression=compression)

    payload: dict[str, object] = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "input_rows": len(feature_frame),
        "dataset_rows": len(dataset),
        "feature_count": len(dataset.feature_columns),
        "feature_columns": list(dataset.feature_columns),
        "metadata_columns": list(dataset.metadata.columns),
        "target_column": "target",
        "config": {
            "history_length": config.history_length,
            "horizon": config.horizon,
            "stride": config.stride,
            "target_column": config.target_column,
            "target_aggregation": config.target_aggregation,
            "target_mode": config.target_mode,
            "classification_threshold": config.classification_threshold,
        },
        "preview": dataframe_records(output_frame.head(3)),
    }
    write_json(payload, summary_output_path)
    return payload


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Build a leakage-safe supervised dataset from feature rows.",
    )
    parser.add_argument("--input-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument(
        "--summary-output-path",
        type=Path,
        default=None,
        help="Optional JSON summary destination.",
    )
    parser.add_argument("--history-length", type=int, default=32)
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--target-column", default="mid_return")
    parser.add_argument(
        "--target-aggregation",
        choices=("sum", "mean", "last"),
        default="sum",
    )
    parser.add_argument(
        "--target-mode",
        choices=("regression", "classification"),
        default="regression",
    )
    parser.add_argument("--classification-threshold", type=float, default=0.0)
    parser.add_argument(
        "--compression",
        default="zstd",
        help="Parquet compression codec, or 'none'. Ignored for CSV output.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    payload = build_dataset_cli(
        input_path=args.input_path,
        output_path=args.output_path,
        summary_output_path=args.summary_output_path,
        history_length=args.history_length,
        horizon=args.horizon,
        stride=args.stride,
        target_column=args.target_column,
        target_aggregation=args.target_aggregation,
        target_mode=args.target_mode,
        classification_threshold=args.classification_threshold,
        compression=args.compression,
    )
    print(write_json(payload, None))


if __name__ == "__main__":
    main()

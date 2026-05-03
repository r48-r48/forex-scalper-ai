"""Run baseline walk-forward validation from flat supervised datasets."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"
for import_path in (SCRIPT_ROOT, SRC_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from cli_utils import dataframe_records, load_frame, write_frame, write_json
from run_backtest import BASELINE_STRATEGY_CHOICES, _select_baseline_specs

from scalper_ai.backtesting import BacktestConfig
from scalper_ai.data.datasets import METADATA_COLUMNS, SupervisedDataset
from scalper_ai.data.labels import (
    TARGET_COLUMN,
    TARGET_END_EVENT_TIMESTAMP_COLUMN,
    TARGET_END_TIMESTAMP_COLUMN,
)
from scalper_ai.data.splits import WalkForwardConfig
from scalper_ai.validation import run_baseline_walk_forward_suite

DATASET_METADATA_COLUMNS = (
    *METADATA_COLUMNS,
    TARGET_END_TIMESTAMP_COLUMN,
    TARGET_END_EVENT_TIMESTAMP_COLUMN,
)


def run_walk_forward_cli(
    *,
    input_path: Path,
    output_path: Path | None = None,
    fold_metrics_output_path: Path | None = None,
    strategy: str = "all",
    train_size: int = 128,
    validation_size: int = 32,
    test_size: int = 32,
    step_size: int | None = None,
    embargo_size: int = 0,
    price_column: str = "mid_price",
    initial_cash: float = 100_000.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
    max_abs_position: float = 1.0,
    max_spread_bps: float = 2.0,
    disable_spread_filter: bool = False,
    compression: str = "zstd",
) -> dict[str, object]:
    """Run default baselines across walk-forward test folds."""

    dataset_frame = load_frame(input_path)
    dataset = supervised_dataset_from_frame(dataset_frame)
    walk_forward_config = WalkForwardConfig(
        train_size=train_size,
        validation_size=validation_size,
        test_size=test_size,
        step_size=step_size,
        embargo_size=embargo_size,
    )
    backtest_config = BacktestConfig(
        price_column=price_column,
        initial_cash=initial_cash,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
    )
    specs = _select_baseline_specs(
        strategy=strategy,
        max_abs_position=max_abs_position,
        max_spread_bps=None if disable_spread_filter else max_spread_bps,
    )
    suite = run_baseline_walk_forward_suite(
        dataset,
        baseline_specs=specs,
        walk_forward_config=walk_forward_config,
        backtest_config=backtest_config,
    )
    if fold_metrics_output_path is not None:
        write_frame(
            suite.fold_metrics,
            fold_metrics_output_path,
            compression=compression,
        )

    payload: dict[str, object] = {
        "input_path": str(input_path),
        "input_rows": len(dataset_frame),
        "dataset_rows": len(dataset),
        "strategy": strategy,
        "strategies": [spec.name for spec in specs],
        "feature_count": len(dataset.feature_columns),
        "walk_forward_config": asdict(walk_forward_config),
        "backtest_config": asdict(backtest_config),
        "risk_limits": {
            "max_abs_position": max_abs_position,
            "max_spread_bps": None if disable_spread_filter else max_spread_bps,
        },
        "fold_metrics": dataframe_records(suite.fold_metrics),
        "summary": dataframe_records(suite.summary),
    }
    write_json(payload, output_path)
    return payload


def supervised_dataset_from_frame(frame: pd.DataFrame) -> SupervisedDataset:
    """Rebuild a supervised dataset object from a flat dataset frame."""

    required_columns = {*METADATA_COLUMNS, TARGET_COLUMN}
    missing = sorted(required_columns.difference(frame.columns))
    if missing:
        raise ValueError(
            "Supervised dataset frame is missing required columns: "
            f"{', '.join(missing)}"
        )

    metadata_columns = [column for column in DATASET_METADATA_COLUMNS if column in frame.columns]
    excluded_columns = {*metadata_columns, TARGET_COLUMN}
    feature_columns = [
        str(column)
        for column in frame.columns
        if str(column) not in excluded_columns
    ]
    if not feature_columns:
        raise ValueError("Supervised dataset frame must contain feature columns.")

    features = frame.loc[:, feature_columns].apply(
        pd.to_numeric,
        errors="raise",
    )
    targets = pd.to_numeric(frame[TARGET_COLUMN], errors="raise").astype(float)
    return SupervisedDataset(
        features=features.reset_index(drop=True),
        targets=targets.reset_index(drop=True).rename(TARGET_COLUMN),
        metadata=frame.loc[:, metadata_columns].reset_index(drop=True),
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Run baseline walk-forward validation on a supervised dataset.",
    )
    parser.add_argument("--input-path", type=Path, required=True)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Optional JSON report destination.",
    )
    parser.add_argument(
        "--fold-metrics-output-path",
        type=Path,
        default=None,
        help="Optional CSV/Parquet fold metrics destination.",
    )
    parser.add_argument(
        "--strategy",
        choices=BASELINE_STRATEGY_CHOICES,
        default="all",
    )
    parser.add_argument("--train-size", type=int, default=128)
    parser.add_argument("--validation-size", type=int, default=32)
    parser.add_argument("--test-size", type=int, default=32)
    parser.add_argument("--step-size", type=int, default=None)
    parser.add_argument("--embargo-size", type=int, default=0)
    _add_backtest_arguments(parser)
    _add_baseline_risk_arguments(parser)
    parser.add_argument(
        "--compression",
        default="zstd",
        help="Parquet compression codec, or 'none'. Ignored for CSV output.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    payload = run_walk_forward_cli(
        input_path=args.input_path,
        output_path=args.output_path,
        fold_metrics_output_path=args.fold_metrics_output_path,
        strategy=args.strategy,
        train_size=args.train_size,
        validation_size=args.validation_size,
        test_size=args.test_size,
        step_size=args.step_size,
        embargo_size=args.embargo_size,
        price_column=args.price_column,
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        commission_bps=args.commission_bps,
        max_abs_position=args.max_abs_position,
        max_spread_bps=args.max_spread_bps,
        disable_spread_filter=args.disable_spread_filter,
        compression=args.compression,
    )
    print(write_json(payload, None))


def _add_backtest_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--price-column", default="mid_price")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument(
        "--spread-bps",
        type=float,
        default=0.0,
        help="Execution spread cost in basis points.",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=0.0,
        help="Execution slippage cost in basis points.",
    )
    parser.add_argument(
        "--commission-bps",
        type=float,
        default=0.0,
        help="Commission cost in basis points.",
    )


def _add_baseline_risk_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-abs-position", type=float, default=1.0)
    parser.add_argument("--max-spread-bps", type=float, default=2.0)
    parser.add_argument(
        "--disable-spread-filter",
        action="store_true",
        help="Disable the baseline entry spread filter.",
    )


if __name__ == "__main__":
    main()

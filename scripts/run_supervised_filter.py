"""Run leakage-safe supervised baseline filter walk-forward evaluation."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"
for import_path in (SCRIPT_ROOT, SRC_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from cli_utils import dataframe_records, load_frame, write_frame, write_json
from run_walk_forward import supervised_dataset_from_frame

from scalper_ai.backtesting import BacktestConfig
from scalper_ai.data.splits import WalkForwardConfig
from scalper_ai.models import SupervisedBaselineFilterConfig, SupervisedBaselineFilterModel
from scalper_ai.validation import run_supervised_filter_walk_forward

FILTER_NAME = "supervised_baseline_filter"


def run_supervised_filter_cli(
    *,
    input_path: Path,
    output_path: Path | None = None,
    fold_metrics_output_path: Path | None = None,
    train_size: int = 128,
    validation_size: int = 32,
    test_size: int = 32,
    step_size: int | None = None,
    embargo_size: int = 0,
    target_threshold: float = 0.0,
    score_threshold: float = 0.0,
    min_scale: float = 1e-12,
    price_column: str = "mid_price",
    initial_cash: float = 100_000.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
    top_features_limit: int = 20,
    compression: str = "zstd",
) -> dict[str, object]:
    """Run train-only-fit/test-only-eval supervised filter validation."""

    if top_features_limit <= 0:
        raise ValueError("top_features_limit must be greater than zero.")

    dataset_frame = load_frame(input_path)
    dataset = supervised_dataset_from_frame(dataset_frame)
    walk_forward_config = WalkForwardConfig(
        train_size=train_size,
        validation_size=validation_size,
        test_size=test_size,
        step_size=step_size,
        embargo_size=embargo_size,
    )
    model_config = SupervisedBaselineFilterConfig(
        target_threshold=target_threshold,
        score_threshold=score_threshold,
        min_scale=min_scale,
    )
    backtest_config = BacktestConfig(
        price_column=price_column,
        initial_cash=initial_cash,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
    )

    result = run_supervised_filter_walk_forward(
        dataset,
        walk_forward_config=walk_forward_config,
        model_config=model_config,
    )
    if fold_metrics_output_path is not None:
        write_frame(
            result.fold_metrics,
            fold_metrics_output_path,
            compression=compression,
        )

    feature_importance = result.feature_importance
    payload: dict[str, object] = {
        "input_path": str(input_path),
        "input_rows": len(dataset_frame),
        "dataset_rows": len(dataset),
        "strategy": FILTER_NAME,
        "strategies": [FILTER_NAME],
        "feature_count": len(dataset.feature_columns),
        "feature_columns": list(dataset.feature_columns),
        "walk_forward_config": asdict(walk_forward_config),
        "model_config": asdict(model_config),
        "backtest_config": asdict(backtest_config),
        "cost_model": {
            "spread_bps": spread_bps,
            "slippage_bps": slippage_bps,
            "commission_bps": commission_bps,
            "applied_to_directional_filter_metrics": False,
        },
        "fold_metrics": dataframe_records(result.fold_metrics),
        "summary": asdict(result.summary),
        "filter_report": {
            "name": FILTER_NAME,
            "fold_count": len(result.folds),
            "top_features": dataframe_records(feature_importance.head(top_features_limit)),
            "feature_importance": dataframe_records(feature_importance),
            "last_fold_model": _model_report(
                result.folds[-1].model,
                top_features_limit=top_features_limit,
            ),
        },
    }
    write_json(payload, output_path)
    return payload


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Run leakage-safe supervised baseline filter walk-forward evaluation.",
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
    _add_walk_forward_arguments(parser)
    _add_model_arguments(parser)
    _add_backtest_arguments(parser)
    parser.add_argument(
        "--top-features-limit",
        type=int,
        default=20,
        help="Number of top features to include in the compact model report.",
    )
    parser.add_argument(
        "--compression",
        default="zstd",
        help="Parquet compression codec, or 'none'. Ignored for CSV output.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    payload = run_supervised_filter_cli(
        input_path=args.input_path,
        output_path=args.output_path,
        fold_metrics_output_path=args.fold_metrics_output_path,
        train_size=args.train_size,
        validation_size=args.validation_size,
        test_size=args.test_size,
        step_size=args.step_size,
        embargo_size=args.embargo_size,
        target_threshold=args.target_threshold,
        score_threshold=args.score_threshold,
        min_scale=args.min_scale,
        price_column=args.price_column,
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        commission_bps=args.commission_bps,
        top_features_limit=args.top_features_limit,
        compression=args.compression,
    )
    print(write_json(payload, None))


def _model_report(
    model: SupervisedBaselineFilterModel,
    *,
    top_features_limit: int,
) -> dict[str, object]:
    importance = model.feature_importance()
    return {
        "bias": model.bias,
        "score_threshold": model.score_threshold,
        "feature_count": len(model.feature_columns),
        "top_features": dataframe_records(importance.head(top_features_limit)),
    }


def _add_walk_forward_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--train-size", type=int, default=128)
    parser.add_argument("--validation-size", type=int, default=32)
    parser.add_argument("--test-size", type=int, default=32)
    parser.add_argument("--step-size", type=int, default=None)
    parser.add_argument("--embargo-size", type=int, default=0)


def _add_model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target-threshold", type=float, default=0.0)
    parser.add_argument("--score-threshold", type=float, default=0.0)
    parser.add_argument("--min-scale", type=float, default=1e-12)


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


if __name__ == "__main__":
    main()

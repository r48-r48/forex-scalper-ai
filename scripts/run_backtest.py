"""Run explicit-cost baseline backtests from replay frames."""

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

from cli_utils import dataframe_records, load_frame, write_json

from scalper_ai.backtesting import BacktestConfig, BaselineStrategySpec
from scalper_ai.backtesting.baselines import build_default_baseline_specs
from scalper_ai.validation import run_baseline_suite

BASELINE_STRATEGY_CHOICES = (
    "all",
    "spread_mean_reversion",
    "ofi_imbalance",
    "volatility_breakout",
)


def run_backtest_cli(
    *,
    input_path: Path,
    output_path: Path | None = None,
    strategy: str = "all",
    price_column: str = "mid_price",
    initial_cash: float = 100_000.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
    max_abs_position: float = 1.0,
    max_spread_bps: float = 2.0,
    disable_spread_filter: bool = False,
) -> dict[str, object]:
    """Run one or all default baselines and return a JSON-ready report."""

    frame = load_frame(input_path)
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
    suite = run_baseline_suite(
        frame,
        baseline_specs=specs,
        backtest_config=backtest_config,
    )

    payload: dict[str, object] = {
        "input_path": str(input_path),
        "input_rows": len(frame),
        "strategy": strategy,
        "strategies": [spec.name for spec in specs],
        "backtest_config": asdict(backtest_config),
        "risk_limits": {
            "max_abs_position": max_abs_position,
            "max_spread_bps": None if disable_spread_filter else max_spread_bps,
        },
        "summary": dataframe_records(suite.summary),
    }
    write_json(payload, output_path)
    return payload


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Run explicit-cost default baseline backtests.",
    )
    parser.add_argument("--input-path", type=Path, required=True)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Optional JSON report destination.",
    )
    parser.add_argument(
        "--strategy",
        choices=BASELINE_STRATEGY_CHOICES,
        default="all",
    )
    _add_backtest_arguments(parser)
    _add_baseline_risk_arguments(parser)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    payload = run_backtest_cli(
        input_path=args.input_path,
        output_path=args.output_path,
        strategy=args.strategy,
        price_column=args.price_column,
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        commission_bps=args.commission_bps,
        max_abs_position=args.max_abs_position,
        max_spread_bps=args.max_spread_bps,
        disable_spread_filter=args.disable_spread_filter,
    )
    print(write_json(payload, None))


def _select_baseline_specs(
    *,
    strategy: str,
    max_abs_position: float,
    max_spread_bps: float | None,
) -> tuple[BaselineStrategySpec, ...]:
    specs = build_default_baseline_specs(
        max_abs_position=max_abs_position,
        max_spread_bps=max_spread_bps,
    )
    if strategy == "all":
        return specs
    selected = tuple(spec for spec in specs if spec.name == strategy)
    if not selected:
        raise ValueError(f"Unknown baseline strategy: {strategy}")
    return selected


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

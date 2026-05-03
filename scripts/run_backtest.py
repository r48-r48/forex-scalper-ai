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

from scalper_ai.backtesting import (
    BacktestConfig,
    BaselineStrategySpec,
    FxSymbolSpec,
    load_fx_symbol_spec,
)
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
    bid_price_column: str | None = None,
    ask_price_column: str | None = None,
    high_price_column: str | None = None,
    low_price_column: str | None = None,
    initial_cash: float = 100_000.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
    spread_bps_column: str | None = None,
    slippage_bps_column: str | None = None,
    commission_bps_column: str | None = None,
    fx_symbol_spec_path: Path | None = None,
    fx_pip_size: float | None = None,
    fx_base_currency: str | None = None,
    fx_quote_currency: str | None = None,
    fx_account_currency: str | None = None,
    fx_contract_size: float | None = None,
    fx_quote_to_account_rate: float | None = None,
    fx_margin_rate: float | None = None,
    fx_swap_long_per_lot: float | None = None,
    fx_swap_short_per_lot: float | None = None,
    fx_rollover_hour_utc: int | None = None,
    margin_call_level: float | None = None,
    stop_loss_price_column: str | None = None,
    take_profit_price_column: str | None = None,
    protective_exit_priority: str = "stop_loss",
    max_abs_position: float = 1.0,
    max_spread_bps: float = 2.0,
    disable_spread_filter: bool = False,
) -> dict[str, object]:
    """Run one or all default baselines and return a JSON-ready report."""

    frame = load_frame(input_path)
    backtest_config = BacktestConfig(
        price_column=price_column,
        bid_price_column=bid_price_column,
        ask_price_column=ask_price_column,
        high_price_column=high_price_column,
        low_price_column=low_price_column,
        initial_cash=initial_cash,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
        spread_bps_column=spread_bps_column,
        slippage_bps_column=slippage_bps_column,
        commission_bps_column=commission_bps_column,
        fx_symbol=_build_fx_symbol_spec(
            spec_path=fx_symbol_spec_path,
            pip_size=fx_pip_size,
            base_currency=fx_base_currency,
            quote_currency=fx_quote_currency,
            account_currency=fx_account_currency,
            contract_size=fx_contract_size,
            quote_to_account_rate=fx_quote_to_account_rate,
            margin_rate=fx_margin_rate,
            swap_long_per_lot=fx_swap_long_per_lot,
            swap_short_per_lot=fx_swap_short_per_lot,
            rollover_hour_utc=fx_rollover_hour_utc,
        ),
        margin_call_level=margin_call_level,
        stop_loss_price_column=stop_loss_price_column,
        take_profit_price_column=take_profit_price_column,
        protective_exit_priority=protective_exit_priority,
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
        bid_price_column=args.bid_price_column,
        ask_price_column=args.ask_price_column,
        high_price_column=args.high_price_column,
        low_price_column=args.low_price_column,
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        commission_bps=args.commission_bps,
        spread_bps_column=args.spread_bps_column,
        slippage_bps_column=args.slippage_bps_column,
        commission_bps_column=args.commission_bps_column,
        fx_symbol_spec_path=args.fx_symbol_spec_path,
        fx_pip_size=args.fx_pip_size,
        fx_base_currency=args.fx_base_currency,
        fx_quote_currency=args.fx_quote_currency,
        fx_account_currency=args.fx_account_currency,
        fx_contract_size=args.fx_contract_size,
        fx_quote_to_account_rate=args.fx_quote_to_account_rate,
        fx_margin_rate=args.fx_margin_rate,
        fx_swap_long_per_lot=args.fx_swap_long_per_lot,
        fx_swap_short_per_lot=args.fx_swap_short_per_lot,
        fx_rollover_hour_utc=args.fx_rollover_hour_utc,
        margin_call_level=args.margin_call_level,
        stop_loss_price_column=args.stop_loss_price_column,
        take_profit_price_column=args.take_profit_price_column,
        protective_exit_priority=args.protective_exit_priority,
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
    parser.add_argument("--bid-price-column", default=None)
    parser.add_argument("--ask-price-column", default=None)
    parser.add_argument("--high-price-column", default=None)
    parser.add_argument("--low-price-column", default=None)
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
    parser.add_argument("--spread-bps-column", default=None)
    parser.add_argument("--slippage-bps-column", default=None)
    parser.add_argument("--commission-bps-column", default=None)
    parser.add_argument(
        "--fx-symbol-spec-path",
        type=Path,
        default=None,
        help="Optional JSON file with broker-style FX symbol assumptions.",
    )
    parser.add_argument(
        "--fx-pip-size",
        type=float,
        default=None,
        help="Enable FX symbol metrics with this pip size, for example 0.0001.",
    )
    parser.add_argument("--fx-base-currency", default=None)
    parser.add_argument("--fx-quote-currency", default=None)
    parser.add_argument("--fx-account-currency", default=None)
    parser.add_argument("--fx-contract-size", type=float, default=None)
    parser.add_argument("--fx-quote-to-account-rate", type=float, default=None)
    parser.add_argument("--fx-margin-rate", type=float, default=None)
    parser.add_argument("--fx-swap-long-per-lot", type=float, default=None)
    parser.add_argument("--fx-swap-short-per-lot", type=float, default=None)
    parser.add_argument("--fx-rollover-hour-utc", type=int, default=None)
    parser.add_argument(
        "--margin-call-level",
        type=float,
        default=None,
        help=(
            "Optional forced-liquidation threshold expressed as "
            "equity / margin_required. Example: 1.0 means 100% margin level."
        ),
    )
    parser.add_argument("--stop-loss-price-column", default=None)
    parser.add_argument("--take-profit-price-column", default=None)
    parser.add_argument(
        "--protective-exit-priority",
        choices=("stop_loss", "take_profit"),
        default="stop_loss",
        help="Priority when a bar path touches both SL and TP.",
    )


def _add_baseline_risk_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-abs-position", type=float, default=1.0)
    parser.add_argument("--max-spread-bps", type=float, default=2.0)
    parser.add_argument(
        "--disable-spread-filter",
        action="store_true",
        help="Disable the baseline entry spread filter.",
    )


def _build_fx_symbol_spec(
    *,
    spec_path: Path | None,
    pip_size: float | None,
    base_currency: str | None,
    quote_currency: str | None,
    account_currency: str | None,
    contract_size: float | None,
    quote_to_account_rate: float | None,
    margin_rate: float | None,
    swap_long_per_lot: float | None,
    swap_short_per_lot: float | None,
    rollover_hour_utc: int | None,
) -> FxSymbolSpec | None:
    manual_fields = {
        "fx_pip_size": pip_size,
        "fx_base_currency": base_currency,
        "fx_quote_currency": quote_currency,
        "fx_account_currency": account_currency,
        "fx_contract_size": contract_size,
        "fx_quote_to_account_rate": quote_to_account_rate,
        "fx_margin_rate": margin_rate,
        "fx_swap_long_per_lot": swap_long_per_lot,
        "fx_swap_short_per_lot": swap_short_per_lot,
        "fx_rollover_hour_utc": rollover_hour_utc,
    }
    provided_manual_fields = [
        field_name for field_name, value in manual_fields.items() if value is not None
    ]
    if spec_path is not None:
        if provided_manual_fields:
            raise ValueError(
                "--fx-symbol-spec-path cannot be combined with manual FX fields: "
                + ", ".join(provided_manual_fields)
                + "."
            )
        return load_fx_symbol_spec(spec_path)
    if pip_size is None:
        if provided_manual_fields:
            raise ValueError(
                "--fx-pip-size is required when using manual FX symbol fields: "
                + ", ".join(provided_manual_fields)
                + "."
            )
        return None
    return FxSymbolSpec(
        base_currency=base_currency if base_currency is not None else "EUR",
        quote_currency=quote_currency if quote_currency is not None else "USD",
        account_currency=account_currency if account_currency is not None else "USD",
        pip_size=pip_size,
        contract_size=contract_size if contract_size is not None else 100_000.0,
        quote_to_account_rate=(
            quote_to_account_rate if quote_to_account_rate is not None else 1.0
        ),
        margin_rate=margin_rate if margin_rate is not None else 0.0,
        swap_long_per_lot=(
            swap_long_per_lot if swap_long_per_lot is not None else 0.0
        ),
        swap_short_per_lot=(
            swap_short_per_lot if swap_short_per_lot is not None else 0.0
        ),
        rollover_hour_utc=rollover_hour_utc if rollover_hour_utc is not None else 21,
    )


if __name__ == "__main__":
    main()

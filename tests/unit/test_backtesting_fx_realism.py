"""Unit tests for opt-in FX realism backtest assumptions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from scalper_ai.backtesting import (
    BacktestConfig,
    FxSymbolSpec,
    fx_symbol_spec_from_mapping,
    load_fx_symbol_spec,
    run_backtest,
)


def test_run_backtest_uses_row_level_execution_cost_regimes() -> None:
    result = run_backtest(
        _market_frame(
            timestamps=[
                datetime(2026, 5, 3, 9, 0, tzinfo=UTC),
                datetime(2026, 5, 3, 9, 1, tzinfo=UTC),
            ],
            prices=[100.0, 100.0],
            signals=[1.0, 0.0],
            spread_bps=[10.0, 20.0],
        ),
        _signal_strategy,
        config=BacktestConfig(
            initial_cash=100_000.0,
            spread_bps_column="spread_bps",
        ),
    )

    assert [fill.fill_price for fill in result.fills] == pytest.approx([100.1, 99.8])
    assert [fill.spread_cost for fill in result.fills] == pytest.approx([0.1, 0.2])
    assert result.metrics.total_pnl == pytest.approx(-0.3)


def test_run_backtest_tracks_fx_pip_margin_and_swap_metrics() -> None:
    result = run_backtest(
        _market_frame(
            timestamps=[
                datetime(2026, 5, 3, 22, 0, tzinfo=UTC),
                datetime(2026, 5, 4, 22, 0, tzinfo=UTC),
            ],
            prices=[1.1, 1.1],
            signals=[100_000.0, 100_000.0],
        ),
        _signal_strategy,
        config=BacktestConfig(
            initial_cash=100_000.0,
            fx_symbol=FxSymbolSpec(
                base_currency="EUR",
                quote_currency="USD",
                account_currency="USD",
                pip_size=0.0001,
                contract_size=100_000.0,
                margin_rate=0.02,
                swap_long_per_lot=2.5,
                rollover_hour_utc=21,
            ),
        ),
    )

    assert result.metrics.pip_value_per_unit == pytest.approx(0.0001)
    assert result.metrics.pip_value_per_lot == pytest.approx(10.0)
    assert result.metrics.swap_cost == pytest.approx(2.5)
    assert result.metrics.total_pnl == pytest.approx(-2.5)
    assert result.metrics.max_margin_required == pytest.approx(2_200.0)
    assert result.metrics.max_margin_utilization == pytest.approx(0.022000550013750344)
    assert result.equity_curve["swap_cost"].tolist() == pytest.approx([0.0, 2.5])
    assert result.equity_curve["margin_required"].tolist() == pytest.approx(
        [2_200.0, 2_200.0]
    )
    assert result.metrics.min_margin_level == pytest.approx(45.45340915929985)
    assert result.metrics.max_effective_leverage == pytest.approx(1.1000275006875173)


def test_run_backtest_forces_liquidation_when_margin_level_breaches_threshold() -> None:
    result = run_backtest(
        _market_frame(
            timestamps=[
                datetime(2026, 5, 3, 9, 0, tzinfo=UTC),
                datetime(2026, 5, 3, 9, 1, tzinfo=UTC),
                datetime(2026, 5, 3, 9, 2, tzinfo=UTC),
            ],
            prices=[1.0, 0.2, 0.2],
            signals=[200_000.0, 200_000.0, 0.0],
        ),
        _signal_strategy,
        config=BacktestConfig(
            initial_cash=100_000.0,
            fx_symbol=FxSymbolSpec(
                base_currency="EUR",
                quote_currency="USD",
                account_currency="USD",
                pip_size=0.0001,
                contract_size=100_000.0,
                margin_rate=0.2,
            ),
            margin_call_level=1.0,
        ),
    )

    assert [fill.side.value for fill in result.fills] == ["buy", "sell"]
    assert [fill.fill_quantity for fill in result.fills] == pytest.approx(
        [200_000.0, 200_000.0]
    )
    assert result.orders[1].strategy_id == "broker_margin_call"
    assert result.orders[1].metadata is not None
    assert result.orders[1].metadata["reason"] == "margin_call"
    assert result.metrics.margin_call_count == 1
    assert result.metrics.liquidation_count == 1
    assert result.metrics.trade_count == 2
    assert result.metrics.final_equity == pytest.approx(-60_000.0)
    assert result.metrics.total_pnl == pytest.approx(-160_000.0)
    assert result.metrics.max_margin_required == pytest.approx(40_000.0)
    assert result.metrics.min_margin_level == pytest.approx(-7.5)
    assert result.metrics.max_effective_leverage == pytest.approx(2.0)
    assert result.equity_curve["liquidated_on_margin_call"].tolist() == [
        False,
        True,
        False,
    ]
    assert result.equity_curve["net_quantity"].tolist() == pytest.approx(
        [200_000.0, 0.0, 0.0]
    )
    assert result.equity_curve["liquidation_count"].tolist() == [0, 1, 1]


def test_run_backtest_liquidates_same_row_when_new_position_breaches_margin_level() -> None:
    result = run_backtest(
        _market_frame(
            timestamps=[datetime(2026, 5, 3, 9, 0, tzinfo=UTC)],
            prices=[1.0],
            signals=[200_000.0],
        ),
        _signal_strategy,
        config=BacktestConfig(
            initial_cash=100_000.0,
            fx_symbol=FxSymbolSpec(
                base_currency="EUR",
                quote_currency="USD",
                account_currency="USD",
                pip_size=0.0001,
                contract_size=100_000.0,
                margin_rate=0.2,
            ),
            margin_call_level=3.0,
        ),
    )

    assert [fill.side.value for fill in result.fills] == ["buy", "sell"]
    assert result.metrics.margin_call_count == 1
    assert result.metrics.liquidation_count == 1
    assert result.metrics.final_equity == pytest.approx(100_000.0)
    assert result.metrics.min_margin_level == pytest.approx(2.5)
    assert result.equity_curve["liquidated_on_margin_call"].tolist() == [True]
    assert result.equity_curve["net_quantity"].tolist() == pytest.approx([0.0])


def test_run_backtest_triggers_prior_stop_loss_from_bar_path() -> None:
    result = run_backtest(
        _market_frame(
            timestamps=[
                datetime(2026, 5, 3, 9, 0, tzinfo=UTC),
                datetime(2026, 5, 3, 9, 1, tzinfo=UTC),
                datetime(2026, 5, 3, 9, 2, tzinfo=UTC),
            ],
            prices=[1.0, 0.96, 0.96],
            signals=[100_000.0, 100_000.0, 0.0],
            high_prices=[1.0, 1.02, 0.96],
            low_prices=[1.0, 0.94, 0.96],
            stop_loss_prices=[0.95, None, None],
            take_profit_prices=[1.1, None, None],
        ),
        _signal_strategy,
        config=BacktestConfig(
            initial_cash=100_000.0,
            high_price_column="high_price",
            low_price_column="low_price",
            stop_loss_price_column="stop_loss_price",
            take_profit_price_column="take_profit_price",
        ),
    )

    assert [fill.side.value for fill in result.fills] == ["buy", "sell"]
    assert result.fills[1].fill_price == pytest.approx(0.95)
    assert result.orders[1].strategy_id == "protective_exit"
    assert result.orders[1].metadata is not None
    assert result.orders[1].metadata["reason"] == "stop_loss"
    assert result.orders[1].metadata["trigger_price"] == pytest.approx(0.95)
    assert result.metrics.protective_exit_count == 1
    assert result.metrics.stop_loss_count == 1
    assert result.metrics.take_profit_count == 0
    assert result.metrics.final_equity == pytest.approx(95_000.0)
    assert result.metrics.total_pnl == pytest.approx(-5_000.0)
    assert result.equity_curve["protective_exit_type"].tolist() == [
        None,
        "stop_loss",
        None,
    ]
    assert result.equity_curve["net_quantity"].tolist() == pytest.approx(
        [100_000.0, 0.0, 0.0]
    )


def test_run_backtest_uses_configured_priority_when_stop_and_take_profit_both_hit() -> None:
    result = run_backtest(
        _market_frame(
            timestamps=[
                datetime(2026, 5, 3, 9, 0, tzinfo=UTC),
                datetime(2026, 5, 3, 9, 1, tzinfo=UTC),
            ],
            prices=[1.0, 1.0],
            signals=[100_000.0, 100_000.0],
            high_prices=[1.0, 1.06],
            low_prices=[1.0, 0.94],
            stop_loss_prices=[0.95, None],
            take_profit_prices=[1.05, None],
        ),
        _signal_strategy,
        config=BacktestConfig(
            initial_cash=100_000.0,
            high_price_column="high_price",
            low_price_column="low_price",
            stop_loss_price_column="stop_loss_price",
            take_profit_price_column="take_profit_price",
            protective_exit_priority="take_profit",
        ),
    )

    assert [fill.side.value for fill in result.fills] == ["buy", "sell"]
    assert result.fills[1].fill_price == pytest.approx(1.05)
    assert result.orders[1].metadata is not None
    assert result.orders[1].metadata["reason"] == "take_profit"
    assert result.metrics.protective_exit_count == 1
    assert result.metrics.stop_loss_count == 0
    assert result.metrics.take_profit_count == 1
    assert result.metrics.total_pnl == pytest.approx(5_000.0)


def test_backtest_config_rejects_invalid_fx_symbol_spec() -> None:
    with pytest.raises(ValueError, match="pip_size must be greater than zero"):
        FxSymbolSpec(
            base_currency="EUR",
            quote_currency="USD",
            account_currency="USD",
            pip_size=0.0,
        )

    with pytest.raises(ValueError, match="rollover_hour_utc"):
        FxSymbolSpec(
            base_currency="EUR",
            quote_currency="USD",
            account_currency="USD",
            pip_size=0.0001,
            rollover_hour_utc=24,
        )

    with pytest.raises(ValueError, match="margin_call_level must be greater than zero"):
        BacktestConfig(margin_call_level=0.0)

    with pytest.raises(ValueError, match="high_price_column and low_price_column"):
        BacktestConfig(high_price_column="high_price")

    with pytest.raises(ValueError, match="are required"):
        BacktestConfig(stop_loss_price_column="stop_loss_price")

    with pytest.raises(ValueError, match="protective_exit_priority"):
        BacktestConfig(
            high_price_column="high_price",
            low_price_column="low_price",
            stop_loss_price_column="stop_loss_price",
            protective_exit_priority="unknown",
        )


def test_fx_symbol_spec_from_mapping_accepts_wrapped_payload() -> None:
    spec = fx_symbol_spec_from_mapping(
        {
            "fx_symbol": {
                "base_currency": "eur",
                "quote_currency": "usd",
                "account_currency": "usd",
                "pip_size": 0.0001,
                "contract_size": 100_000,
                "quote_to_account_rate": 1,
                "margin_rate": 0.02,
                "swap_long_per_lot": -4.1,
                "swap_short_per_lot": 1.2,
                "rollover_hour_utc": 22,
            }
        }
    )

    assert spec.base_currency == "EUR"
    assert spec.quote_currency == "USD"
    assert spec.account_currency == "USD"
    assert spec.pip_size == pytest.approx(0.0001)
    assert spec.contract_size == pytest.approx(100_000.0)
    assert spec.margin_rate == pytest.approx(0.02)
    assert spec.swap_long_per_lot == pytest.approx(-4.1)
    assert spec.swap_short_per_lot == pytest.approx(1.2)
    assert spec.rollover_hour_utc == 22


def test_load_fx_symbol_spec_reads_flat_json_file(tmp_path: Path) -> None:
    spec_path = tmp_path / "eurusd-symbol.json"
    spec_path.write_text(
        json.dumps(
            {
                "base_currency": "EUR",
                "quote_currency": "USD",
                "account_currency": "USD",
                "pip_size": 0.0001,
                "margin_rate": 0.02,
            }
        ),
        encoding="utf-8",
    )

    spec = load_fx_symbol_spec(spec_path)

    assert spec.pip_value_per_lot == pytest.approx(10.0)
    assert spec.margin_rate == pytest.approx(0.02)


def test_fx_symbol_spec_loader_rejects_unknown_and_missing_fields(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Unknown FX symbol spec fields: digits"):
        fx_symbol_spec_from_mapping(
            {
                "base_currency": "EUR",
                "quote_currency": "USD",
                "account_currency": "USD",
                "pip_size": 0.0001,
                "digits": 5,
            }
        )

    with pytest.raises(ValueError, match="Missing required FX symbol spec fields"):
        fx_symbol_spec_from_mapping(
            {
                "base_currency": "EUR",
                "quote_currency": "USD",
                "account_currency": "USD",
            }
        )

    spec_path = tmp_path / "invalid-symbol.json"
    spec_path.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
    with pytest.raises(ValueError, match="FX symbol spec file must be a JSON object"):
        load_fx_symbol_spec(spec_path)


def test_run_backtest_rejects_negative_row_level_costs() -> None:
    frame = _market_frame(
        timestamps=[datetime(2026, 5, 3, 9, 0, tzinfo=UTC)],
        prices=[100.0],
        signals=[1.0],
        spread_bps=[-1.0],
    )

    with pytest.raises(ValueError, match="spread_bps must contain non-negative"):
        run_backtest(
            frame,
            _signal_strategy,
            config=BacktestConfig(spread_bps_column="spread_bps"),
        )


def test_run_backtest_rejects_invalid_bar_path_columns() -> None:
    frame = _market_frame(
        timestamps=[datetime(2026, 5, 3, 9, 0, tzinfo=UTC)],
        prices=[1.0],
        signals=[1.0],
        high_prices=[0.99],
        low_prices=[0.98],
        stop_loss_prices=[0.95],
    )

    with pytest.raises(ValueError, match="price_column must be between"):
        run_backtest(
            frame,
            _signal_strategy,
            config=BacktestConfig(
                high_price_column="high_price",
                low_price_column="low_price",
                stop_loss_price_column="stop_loss_price",
            ),
        )


def _market_frame(
    *,
    timestamps: list[datetime],
    prices: list[float],
    signals: list[float],
    spread_bps: list[float] | None = None,
    high_prices: list[float] | None = None,
    low_prices: list[float] | None = None,
    stop_loss_prices: list[float | None] | None = None,
    take_profit_prices: list[float | None] | None = None,
) -> pd.DataFrame:
    assert len(timestamps) == len(prices) == len(signals)
    rows: list[dict[str, object]] = []
    for index, (timestamp, price, signal) in enumerate(
        zip(timestamps, prices, signals, strict=True)
    ):
        row: dict[str, object] = {
            "symbol": "EURUSD",
            "event_timestamp": timestamp,
            "available_timestamp": timestamp + timedelta(milliseconds=50),
            "mid_price": price,
            "signal": signal,
        }
        if spread_bps is not None:
            row["spread_bps"] = spread_bps[index]
        if high_prices is not None:
            row["high_price"] = high_prices[index]
        if low_prices is not None:
            row["low_price"] = low_prices[index]
        if stop_loss_prices is not None:
            row["stop_loss_price"] = stop_loss_prices[index]
        if take_profit_prices is not None:
            row["take_profit_price"] = take_profit_prices[index]
        rows.append(row)
    return pd.DataFrame.from_records(rows)


def _signal_strategy(event, state) -> float:
    del state
    return float(event.row_payload["signal"])

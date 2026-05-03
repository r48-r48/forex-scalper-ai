"""Unit tests for opt-in FX realism backtest assumptions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from scalper_ai.backtesting import BacktestConfig, FxSymbolSpec, run_backtest


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


def _market_frame(
    *,
    timestamps: list[datetime],
    prices: list[float],
    signals: list[float],
    spread_bps: list[float] | None = None,
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
        rows.append(row)
    return pd.DataFrame.from_records(rows)


def _signal_strategy(event, state) -> float:
    del state
    return float(event.row_payload["signal"])

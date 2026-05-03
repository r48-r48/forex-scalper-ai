"""Unit tests for bid/ask-aware historical backtests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from scalper_ai.backtesting import BacktestConfig, run_backtest


def test_run_backtest_without_bid_ask_keeps_mid_price_execution() -> None:
    result = run_backtest(
        _market_frame(
            mid_prices=[100.0, 101.0],
            bid_prices=[99.9, 100.9],
            ask_prices=[100.1, 101.1],
            signals=[1.0, 0.0],
        ).drop(columns=["bid_price", "ask_price"]),
        _signal_strategy,
        config=BacktestConfig(initial_cash=100_000.0),
    )

    assert [fill.fill_price for fill in result.fills] == pytest.approx([100.0, 101.0])
    assert result.metrics.total_pnl == pytest.approx(1.0)


def test_run_backtest_uses_ask_for_buys_and_bid_for_sells_when_configured() -> None:
    result = run_backtest(
        _market_frame(
            mid_prices=[100.0, 101.0],
            bid_prices=[99.9, 100.9],
            ask_prices=[100.1, 101.1],
            signals=[1.0, 0.0],
        ),
        _signal_strategy,
        config=BacktestConfig(
            initial_cash=100_000.0,
            bid_price_column="bid_price",
            ask_price_column="ask_price",
        ),
    )

    assert [fill.fill_price for fill in result.fills] == pytest.approx([100.1, 100.9])
    assert result.fills[0].side.value == "buy"
    assert result.fills[1].side.value == "sell"
    assert result.metrics.total_pnl == pytest.approx(0.8)


def test_run_backtest_rejects_crossed_bid_ask_quotes() -> None:
    frame = _market_frame(
        mid_prices=[100.0],
        bid_prices=[100.2],
        ask_prices=[100.1],
        signals=[0.0],
    )

    with pytest.raises(ValueError, match="bid_price_column must not exceed ask_price_column"):
        run_backtest(
            frame,
            _flat_strategy,
            config=BacktestConfig(
                bid_price_column="bid_price",
                ask_price_column="ask_price",
            ),
        )


def test_backtest_config_requires_bid_and_ask_columns_together() -> None:
    with pytest.raises(ValueError, match="configured together"):
        BacktestConfig(bid_price_column="bid_price")


def _market_frame(
    *,
    mid_prices: list[float],
    bid_prices: list[float],
    ask_prices: list[float],
    signals: list[float],
) -> pd.DataFrame:
    assert len(mid_prices) == len(bid_prices) == len(ask_prices) == len(signals)
    base_time = datetime(2026, 5, 3, 9, 0, tzinfo=UTC)
    records: list[dict[str, object]] = []
    for index, (mid_price, bid_price, ask_price, signal) in enumerate(
        zip(mid_prices, bid_prices, ask_prices, signals, strict=True)
    ):
        timestamp = base_time + timedelta(minutes=index)
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp,
                "available_timestamp": timestamp,
                "mid_price": mid_price,
                "bid_price": bid_price,
                "ask_price": ask_price,
                "signal": signal,
            }
        )
    return pd.DataFrame.from_records(records)


def _signal_strategy(event, state) -> float:
    del state
    return float(event.row_payload["signal"])


def _flat_strategy(event, state) -> float:
    del event, state
    return 0.0

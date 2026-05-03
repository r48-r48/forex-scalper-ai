"""Integration tests for deterministic PHASE 9 replay backtests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from scalper_ai.backtesting import BacktestConfig, run_backtest


def test_run_backtest_replays_target_position_strategy_deterministically() -> None:
    result = run_backtest(
        _market_frame(
            prices=[100.0, 99.0, 101.0, 101.0],
            signals=[1.0, 1.0, 0.0, 0.0],
        ),
        _signal_strategy,
        config=BacktestConfig(initial_cash=100_000.0),
    )

    assert len(result.orders) == 2
    assert len(result.fills) == 2
    assert result.position_history[-1].net_quantity == pytest.approx(0.0)
    assert result.metrics.final_equity == pytest.approx(100_001.0)
    assert result.metrics.total_pnl == pytest.approx(1.0)
    assert result.metrics.max_drawdown == pytest.approx(0.00001)
    assert result.metrics.trade_count == 2
    assert result.metrics.turnover_quote == pytest.approx(201.0)


def test_run_backtest_with_flat_strategy_keeps_zero_fills_and_flat_equity() -> None:
    result = run_backtest(
        _market_frame(
            prices=[100.0, 99.0, 101.0],
            signals=[0.0, 0.0, 0.0],
        ),
        _flat_strategy,
        config=BacktestConfig(initial_cash=100_000.0),
    )

    assert len(result.fills) == 0
    assert result.metrics.trade_count == 0
    assert result.metrics.turnover_quote == pytest.approx(0.0)
    assert result.equity_curve["equity"].tolist() == pytest.approx(
        [100_000.0, 100_000.0, 100_000.0]
    )
    assert all(position.net_quantity == 0.0 for position in result.position_history)


def _market_frame(*, prices: list[float], signals: list[float]) -> pd.DataFrame:
    base_time = datetime(2026, 3, 27, 9, 0, 0, tzinfo=UTC)
    assert len(prices) == len(signals)
    records: list[dict[str, object]] = []
    for index, (price, signal) in enumerate(zip(prices, signals, strict=True)):
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": base_time + timedelta(minutes=index),
                "available_timestamp": base_time + timedelta(minutes=index),
                "mid_price": price,
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

"""Unit tests for the execution-aware backtesting simulator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scalper_ai.backtesting import (
    BacktestConfig,
    ExecutionSimulatorConfig,
    SimulatedOrderStatus,
    run_execution_aware_backtest,
)


def test_execution_aware_backtest_injects_latency_and_slippage_metrics() -> None:
    result = run_execution_aware_backtest(
        _market_frame(prices=[100.0, 101.0, 102.0], signals=[10.0, 10.0, 10.0]),
        _signal_strategy,
        config=ExecutionSimulatorConfig(
            base=BacktestConfig(initial_cash=100_000.0, slippage_bps=5.0),
            latency_steps=1,
        ),
    )

    assert len(result.orders) == 1
    assert len(result.fills) == 1
    assert result.fills[0].event_timestamp == _timestamp(1)
    assert result.fills[0].fill_price > 101.0
    assert result.execution_orders[0].status is SimulatedOrderStatus.FILLED
    assert result.execution_orders[0].latency_steps == 1
    assert result.metrics.fill_ratio == pytest.approx(1.0)
    assert result.metrics.average_slippage_bps > 0.0
    assert result.metrics.average_latency_steps == pytest.approx(1.0)


def test_execution_aware_backtest_handles_forced_partial_fills() -> None:
    result = run_execution_aware_backtest(
        _market_frame(
            prices=[100.0, 101.0, 102.0],
            signals=[10.0, 10.0, 10.0],
            partial_fill_ratios=[None, 0.4, 1.0],
        ),
        _signal_strategy,
        config=ExecutionSimulatorConfig(latency_steps=1),
    )

    assert [fill.fill_quantity for fill in result.fills] == pytest.approx([4.0, 6.0])
    assert result.execution_orders[0].status is SimulatedOrderStatus.FILLED
    assert result.execution_orders[0].had_partial_fill is True
    assert result.metrics.partial_fill_count == 1
    assert result.metrics.fill_ratio == pytest.approx(1.0)


def test_execution_aware_backtest_uses_queue_position_and_liquidity_caps() -> None:
    result = run_execution_aware_backtest(
        _market_frame(
            prices=[100.0, 101.0, 102.0],
            signals=[10.0, 10.0, 10.0],
            queue_ahead_quantities=[10.0, None, None],
            available_liquidity=[None, 8.0, 7.0],
        ),
        _signal_strategy,
        config=ExecutionSimulatorConfig(latency_steps=1),
    )

    assert len(result.fills) == 1
    assert result.fills[0].fill_quantity == pytest.approx(5.0)
    assert result.execution_orders[0].status is SimulatedOrderStatus.CANCELLED
    assert result.execution_orders[0].queue_ahead_quantity == pytest.approx(0.0)
    assert result.metrics.fill_ratio == pytest.approx(0.5)
    assert result.metrics.cancel_ratio == pytest.approx(1.0)


def test_execution_aware_backtest_models_cancel_replace_race_fill() -> None:
    result = run_execution_aware_backtest(
        _market_frame(
            prices=[100.0, 101.0, 102.0],
            signals=[10.0, -10.0, -10.0],
            cancel_replace_races=[False, True, False],
        ),
        _signal_strategy,
        config=ExecutionSimulatorConfig(
            latency_steps=3,
            cancel_replace_race_fill_ratio=0.5,
        ),
    )

    first_order = result.execution_orders[0]
    assert result.fills[0].fill_quantity == pytest.approx(5.0)
    assert first_order.status is SimulatedOrderStatus.CANCELLED
    assert first_order.cancel_reason == "cancel_replace"
    assert first_order.had_partial_fill is True
    assert result.metrics.partial_fill_count == 1
    assert result.metrics.cancel_ratio == pytest.approx(1.0)


def test_execution_aware_backtest_rejects_closed_and_stale_market_submissions() -> None:
    closed_result = run_execution_aware_backtest(
        _market_frame(
            prices=[100.0],
            signals=[10.0],
            market_statuses=["closed"],
        ),
        _signal_strategy,
    )
    stale_result = run_execution_aware_backtest(
        _market_frame(
            prices=[100.0],
            signals=[10.0],
            event_lag_seconds=[3.0],
        ),
        _signal_strategy,
        config=ExecutionSimulatorConfig(stale_after_seconds=2.0),
    )

    assert closed_result.execution_orders[0].status is SimulatedOrderStatus.REJECTED
    assert closed_result.execution_orders[0].rejection_reason == "market_status:closed"
    assert closed_result.metrics.reject_ratio == pytest.approx(1.0)
    assert stale_result.execution_orders[0].status is SimulatedOrderStatus.REJECTED
    assert stale_result.execution_orders[0].rejection_reason == "stale_market_data"
    assert stale_result.metrics.reject_ratio == pytest.approx(1.0)


def _market_frame(
    *,
    prices: list[float],
    signals: list[float],
    partial_fill_ratios: list[float | None] | None = None,
    queue_ahead_quantities: list[float | None] | None = None,
    available_liquidity: list[float | None] | None = None,
    cancel_replace_races: list[bool] | None = None,
    market_statuses: list[str] | None = None,
    event_lag_seconds: list[float] | None = None,
) -> pd.DataFrame:
    assert len(prices) == len(signals)
    base_time = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
    records: list[dict[str, object]] = []
    for index, (price, signal) in enumerate(zip(prices, signals)):  # noqa: B905
        lag_seconds = _optional_at(event_lag_seconds, index, default=0.0)
        available_timestamp = base_time + timedelta(minutes=index)
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": available_timestamp - timedelta(seconds=float(lag_seconds)),
                "available_timestamp": available_timestamp,
                "mid_price": price,
                "signal": signal,
                "partial_fill_ratio": _optional_at(partial_fill_ratios, index),
                "queue_ahead_quantity": _optional_at(queue_ahead_quantities, index),
                "available_liquidity": _optional_at(available_liquidity, index),
                "cancel_replace_race": _optional_at(cancel_replace_races, index, default=False),
                "market_status": _optional_at(market_statuses, index, default="open"),
            }
        )
    return pd.DataFrame.from_records(records)


def _optional_at(values, index: int, *, default=None):
    if values is None:
        return default
    return values[index]


def _timestamp(minutes: int) -> datetime:
    return datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)  # noqa: UP017


def _signal_strategy(event, state) -> float:
    del state
    return float(event.row_payload["signal"])

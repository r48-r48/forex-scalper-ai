"""Unit tests for backtesting fill simulation and netting accounting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from scalper_ai.backtesting import BacktestConfig, run_backtest
from scalper_ai.backtesting.accounting import (
    apply_fill_to_cash,
    apply_fill_to_position,
    simulate_market_fill,
)
from scalper_ai.domain import OrderIntent, OrderSide, OrderType


def _timestamp(minutes: int) -> datetime:
    return datetime(2026, 3, 27, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def test_simulate_market_fill_decomposes_buy_and_sell_costs() -> None:
    buy_fill = simulate_market_fill(
        _market_order(side=OrderSide.BUY, quantity=2.0, intent_id="intent-buy"),
        fill_id="fill-buy",
        event_timestamp=_timestamp(0),
        received_timestamp=_timestamp(0),
        mark_price=100.0,
        spread_bps=10.0,
        slippage_bps=5.0,
        commission_bps=20.0,
    )
    sell_fill = simulate_market_fill(
        _market_order(side=OrderSide.SELL, quantity=2.0, intent_id="intent-sell"),
        fill_id="fill-sell",
        event_timestamp=_timestamp(1),
        received_timestamp=_timestamp(1),
        mark_price=100.0,
        spread_bps=10.0,
        slippage_bps=5.0,
        commission_bps=20.0,
    )

    assert buy_fill.fill_price == pytest.approx(100.15)
    assert buy_fill.spread_cost == pytest.approx(0.2)
    assert buy_fill.slippage_cost == pytest.approx(0.1)
    assert buy_fill.commission == pytest.approx(0.4006)

    assert sell_fill.fill_price == pytest.approx(99.85)
    assert sell_fill.spread_cost == pytest.approx(0.2)
    assert sell_fill.slippage_cost == pytest.approx(0.1)
    assert sell_fill.commission == pytest.approx(0.3994)


def test_apply_fill_to_position_handles_add_reduce_flip_and_close() -> None:
    position = apply_fill_to_position(
        None,
        _fill(OrderSide.BUY, 2.0, 100.0, "fill-1"),
        mark_price=100.0,
    )
    assert position.net_quantity == pytest.approx(2.0)
    assert position.average_entry_price == pytest.approx(100.0)
    assert position.realized_pnl == pytest.approx(0.0)

    position = apply_fill_to_position(
        position,
        _fill(OrderSide.BUY, 1.0, 110.0, "fill-2"),
        mark_price=110.0,
    )
    assert position.net_quantity == pytest.approx(3.0)
    assert position.average_entry_price == pytest.approx(103.3333333333)

    position = apply_fill_to_position(
        position,
        _fill(OrderSide.SELL, 1.0, 120.0, "fill-3"),
        mark_price=120.0,
    )
    assert position.net_quantity == pytest.approx(2.0)
    assert position.average_entry_price == pytest.approx(103.3333333333)
    assert position.realized_pnl == pytest.approx(16.6666666667)

    position = apply_fill_to_position(
        position,
        _fill(OrderSide.SELL, 4.0, 90.0, "fill-4"),
        mark_price=90.0,
    )
    assert position.net_quantity == pytest.approx(-2.0)
    assert position.average_entry_price == pytest.approx(90.0)
    assert position.realized_pnl == pytest.approx(-10.0)

    position = apply_fill_to_position(
        position,
        _fill(OrderSide.BUY, 2.0, 80.0, "fill-5"),
        mark_price=80.0,
    )
    assert position.net_quantity == pytest.approx(0.0)
    assert position.average_entry_price == pytest.approx(0.0)
    assert position.realized_pnl == pytest.approx(10.0)
    assert position.unrealized_pnl == pytest.approx(0.0)


def test_run_backtest_updates_equity_and_drawdown_without_trades_on_later_steps() -> None:
    frame = _market_frame(prices=[100.0, 90.0, 95.0], signals=[1.0, 1.0, 1.0])

    result = run_backtest(
        frame,
        _signal_strategy,
        config=BacktestConfig(initial_cash=100_000.0),
    )

    assert len(result.fills) == 1
    assert result.equity_curve["equity"].tolist() == pytest.approx([100_000.0, 99_990.0, 99_995.0])
    assert result.equity_curve["drawdown"].tolist() == pytest.approx([0.0, 0.0001, 0.00005])
    assert result.metrics.max_drawdown == pytest.approx(0.0001)


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (
            pd.DataFrame(
                {
                    "symbol": ["EURUSD"],
                    "event_timestamp": [_timestamp(0)],
                    "mid_price": [100.0],
                }
            ),
            "missing required columns",
        ),
        (
            pd.DataFrame(
                {
                    "symbol": ["EURUSD"],
                    "event_timestamp": [datetime(2026, 3, 27, 9, 0, 0)],
                    "available_timestamp": [datetime(2026, 3, 27, 9, 0, 1)],
                    "mid_price": [100.0],
                }
            ),
            "UTC-aware timestamps",
        ),
        (
            pd.DataFrame(
                {
                    "symbol": ["EURUSD", "GBPUSD"],
                    "event_timestamp": [_timestamp(0), _timestamp(1)],
                    "available_timestamp": [_timestamp(0), _timestamp(1)],
                    "mid_price": [100.0, 101.0],
                }
            ),
            "exactly one symbol",
        ),
    ],
)
def test_run_backtest_rejects_invalid_frames(frame: pd.DataFrame, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        run_backtest(frame, _flat_strategy)


def _market_order(*, side: OrderSide, quantity: float, intent_id: str) -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id,
        strategy_id="unit-test-strategy",
        symbol="EURUSD",
        created_at=_timestamp(0),
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        paper=True,
    )


def _fill(side: OrderSide, quantity: float, fill_price: float, fill_id: str):
    fill = simulate_market_fill(
        _market_order(side=side, quantity=quantity, intent_id=f"{fill_id}-intent"),
        fill_id=fill_id,
        event_timestamp=_timestamp(0),
        received_timestamp=_timestamp(0),
        mark_price=fill_price,
    )
    cash_balance = apply_fill_to_cash(100_000.0, fill)
    expected_cash_delta = (
        fill.fill_quantity * fill.fill_price
        if side is OrderSide.BUY
        else -fill.fill_quantity * fill.fill_price
    )
    assert cash_balance == pytest.approx(
        100_000.0 - expected_cash_delta
    )
    return fill


def _market_frame(*, prices: list[float], signals: list[float]) -> pd.DataFrame:
    assert len(prices) == len(signals)
    records: list[dict[str, object]] = []
    for index, (price, signal) in enumerate(zip(prices, signals, strict=True)):
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": _timestamp(index),
                "available_timestamp": _timestamp(index),
                "mid_price": price,
                "signal": signal,
            }
        )
    return pd.DataFrame.from_records(records)


def _signal_strategy(event, state) -> float:
    del state
    signal = float(event.row_payload["signal"])
    if signal > 0:
        return 1.0
    if signal < 0:
        return -1.0
    return 0.0


def _flat_strategy(event, state) -> float:
    del event, state
    return 0.0

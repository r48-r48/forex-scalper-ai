"""Unit tests for P1.2 baseline target-position strategies."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scalper_ai.backtesting import (
    BacktestEvent,
    BacktestState,
    BaselineRiskConfig,
    OfiImbalanceConfig,
    OfiImbalanceStrategy,
    SpreadMeanReversionConfig,
    SpreadMeanReversionStrategy,
    VolatilityBreakoutConfig,
    VolatilityBreakoutStrategy,
)
from scalper_ai.backtesting.accounting import mark_position


def test_spread_mean_reversion_trades_against_current_return_only() -> None:
    strategy = SpreadMeanReversionStrategy(
        SpreadMeanReversionConfig(
            risk=BaselineRiskConfig(max_abs_position=2.0, max_spread_bps=1.0),
            entry_return_threshold=0.001,
            exit_return_threshold=0.0002,
        )
    )

    assert strategy(_event(mid_return=-0.002, spread_bps=0.5), _state()) == pytest.approx(2.0)
    assert strategy(_event(mid_return=0.002, spread_bps=0.5), _state()) == pytest.approx(-2.0)
    assert strategy(_event(mid_return=0.002, spread_bps=3.0), _state()) == pytest.approx(0.0)
    assert strategy(_event(mid_return=0.00001, spread_bps=0.5), _state(1.5)) == pytest.approx(0.0)


def test_ofi_imbalance_uses_ofi_then_mlofi_and_flattens_without_l2() -> None:
    strategy = OfiImbalanceStrategy(
        OfiImbalanceConfig(
            risk=BaselineRiskConfig(max_abs_position=3.0, max_spread_bps=2.0),
            entry_threshold=10.0,
            exit_threshold=2.0,
        )
    )

    assert strategy(
        _event(ofi=12.0, mlofi_total=-50.0, spread_bps=0.5),
        _state(),
    ) == pytest.approx(3.0)
    assert strategy(_event(mlofi_total=-12.0, spread_bps=0.5), _state()) == pytest.approx(-3.0)
    assert strategy(_event(spread_bps=0.5), _state(2.0)) == pytest.approx(0.0)
    assert strategy(_event(ofi=20.0, spread_bps=5.0), _state(2.0)) == pytest.approx(0.0)


def test_volatility_breakout_follows_current_return_breakout() -> None:
    strategy = VolatilityBreakoutStrategy(
        VolatilityBreakoutConfig(
            risk=BaselineRiskConfig(max_abs_position=1.5, max_spread_bps=2.0),
            absolute_return_threshold=0.001,
            entry_volatility_multiplier=2.0,
            exit_volatility_multiplier=0.5,
        )
    )

    assert strategy(
        _event(mid_return=0.003, realized_volatility=0.001, spread_bps=0.5),
        _state(),
    ) == pytest.approx(1.5)
    assert strategy(
        _event(mid_return=-0.003, realized_volatility=0.001, spread_bps=0.5),
        _state(),
    ) == pytest.approx(-1.5)
    assert strategy(
        _event(mid_return=0.0001, realized_volatility=0.001, spread_bps=0.5),
        _state(1.0),
    ) == pytest.approx(0.0)
    assert strategy(
        _event(mid_return=0.003, realized_volatility=0.001, spread_bps=5.0),
        _state(),
    ) == pytest.approx(0.0)


def test_baseline_strategy_rejects_non_finite_signal_values() -> None:
    strategy = SpreadMeanReversionStrategy()

    with pytest.raises(ValueError, match="must be finite"):
        strategy(_event(mid_return=float("inf")), _state())


def _event(**payload: float) -> BacktestEvent:
    timestamp = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
    row_payload = {
        "symbol": "EURUSD",
        "event_timestamp": timestamp,
        "available_timestamp": timestamp,
        "mid_price": 100.0,
    }
    row_payload.update(payload)
    return BacktestEvent(
        symbol="EURUSD",
        event_timestamp=row_payload["event_timestamp"],
        available_timestamp=row_payload["available_timestamp"],
        mark_price=100.0,
        row_payload=row_payload,
    )


def _state(net_quantity: float = 0.0) -> BacktestState:
    timestamp = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
    position = mark_position(
        None,
        symbol="EURUSD",
        timestamp=timestamp,
        mark_price=100.0,
    )
    if net_quantity:
        position = position.model_copy(update={"net_quantity": net_quantity})
    return BacktestState(
        current_position=position,
        cash_balance=100_000.0,
        equity=100_000.0,
        peak_equity=100_000.0,
        drawdown=0.0,
        trade_count=0,
        turnover_quote=0.0,
    )

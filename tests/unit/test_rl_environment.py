"""Unit tests for RL trading environment transitions and rewards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from scalper_ai.rl.config import TradingEnvironmentConfig
from scalper_ai.rl.environment import TradingAction, TradingEnvironment


def test_environment_step_accounts_for_price_move_and_costs() -> None:
    environment = TradingEnvironment(
        _market_frame([100.0, 101.0, 102.0]),
        config=TradingEnvironmentConfig(
            price_column="mid_price",
            timestamp_column="available_timestamp",
            feature_columns=("mid_price", "signal"),
            position_size=1.0,
            spread_bps=10.0,
            slippage_bps=5.0,
            holding_cost_bps=2.0,
        ),
    )
    environment.reset()

    result = environment.step(TradingAction.LONG)

    expected_spread = 100.0 * (10.0 / 10_000.0)
    expected_slippage = 100.0 * (5.0 / 10_000.0)
    expected_holding = 100.0 * (2.0 / 10_000.0)
    expected_pnl = 1.0 * (101.0 - 100.0)
    expected_reward = expected_pnl - expected_spread - expected_slippage - expected_holding

    assert result.reward == pytest.approx(expected_reward)
    assert result.info["price_pnl"] == pytest.approx(expected_pnl)
    assert result.info["spread_cost"] == pytest.approx(expected_spread)
    assert result.info["slippage_cost"] == pytest.approx(expected_slippage)
    assert result.info["holding_cost"] == pytest.approx(expected_holding)
    assert result.info["target_position"] == pytest.approx(1.0)
    assert environment.current_position == pytest.approx(1.0)


def test_environment_rejects_step_after_done() -> None:
    environment = TradingEnvironment(_market_frame([100.0, 100.5]))
    environment.reset()

    result = environment.step(TradingAction.FLAT)

    assert result.done is True
    with pytest.raises(RuntimeError, match="finished environment"):
        environment.step(TradingAction.FLAT)


def _market_frame(prices: list[float]) -> pd.DataFrame:
    base_time = datetime(2026, 3, 26, 9, 0, 0, tzinfo=UTC)
    records: list[dict[str, object]] = []
    for index, price in enumerate(prices):
        records.append(
            {
                "available_timestamp": base_time + timedelta(minutes=index),
                "mid_price": price,
                "signal": 0.1 * index,
            }
        )
    return pd.DataFrame.from_records(records)

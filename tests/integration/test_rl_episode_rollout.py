"""Integration tests for multi-step RL episode behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import torch

from scalper_ai.rl.config import PolicyNetworkConfig, TradingEnvironmentConfig
from scalper_ai.rl.environment import TradingEnvironment
from scalper_ai.rl.policy import TradingPolicyNetwork
from scalper_ai.rl.training import rollout_policy_episode


def test_rollout_policy_episode_runs_through_entire_market_sequence() -> None:
    torch.manual_seed(5)
    environment = TradingEnvironment(
        _market_frame(),
        config=TradingEnvironmentConfig(
            price_column="mid_price",
            timestamp_column="available_timestamp",
            feature_columns=("mid_price", "signal"),
            position_size=1.0,
            spread_bps=1.0,
            slippage_bps=0.5,
            holding_cost_bps=0.1,
        ),
    )
    policy = TradingPolicyNetwork(PolicyNetworkConfig(input_size=3, hidden_size=6))

    rollout = rollout_policy_episode(environment, policy, deterministic=True)

    assert rollout.steps == 3
    assert rollout.observations.shape == (3, 3)
    assert rollout.action_indices.shape == (3,)
    assert rollout.rewards.shape == (3,)
    assert torch.isfinite(torch.tensor(rollout.total_reward))
    assert torch.isfinite(torch.tensor(rollout.total_pnl))


def _market_frame() -> pd.DataFrame:
    base_time = datetime(2026, 3, 26, 9, 0, 0, tzinfo=timezone.utc)
    records: list[dict[str, object]] = []
    for index, price in enumerate([100.0, 100.8, 100.2, 101.0]):
        records.append(
            {
                "available_timestamp": base_time + timedelta(minutes=index),
                "mid_price": price,
                "signal": 0.05 * (index - 1),
            }
        )
    return pd.DataFrame.from_records(records)

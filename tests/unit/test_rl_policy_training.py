"""Unit tests for RL policy and training helpers."""

from __future__ import annotations

import torch

from scalper_ai.rl.config import PolicyNetworkConfig, PolicyTrainingConfig
from scalper_ai.rl.policy import TradingPolicyNetwork
from scalper_ai.rl.training import PolicyTrainingBatch, discounted_returns, train_policy_batch


def test_discounted_returns_monotonically_propagate_future_rewards() -> None:
    returns = discounted_returns([1.0, 0.0, 2.0], gamma=0.5, normalize=False)

    assert returns.tolist() == [1.5, 1.0, 2.0]


def test_train_policy_batch_returns_finite_metrics() -> None:
    torch.manual_seed(11)
    policy = TradingPolicyNetwork(PolicyNetworkConfig(input_size=3, hidden_size=8))
    optimizer = torch.optim.Adam(policy.parameters(), lr=1e-2)
    batch = PolicyTrainingBatch(
        observations=torch.tensor(
            [[0.0, 0.1, 0.0], [0.2, -0.1, 1.0], [-0.1, 0.3, -1.0]],
            dtype=torch.float32,
        ),
        action_indices=torch.tensor([0, 2, 1], dtype=torch.int64),
        returns=torch.tensor([1.0, 0.5, -0.2], dtype=torch.float32),
    )

    result = train_policy_batch(
        policy,
        optimizer,
        batch,
        config=PolicyTrainingConfig(gamma=0.99, entropy_coefficient=0.01, grad_clip_norm=1.0),
    )

    assert torch.isfinite(torch.tensor(result.loss))
    assert torch.isfinite(torch.tensor(result.mean_return))
    assert torch.isfinite(torch.tensor(result.mean_entropy))

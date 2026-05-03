"""Baseline policy network and action mapping helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import torch
from torch import nn
from torch.distributions import Categorical

from scalper_ai.rl.config import PolicyNetworkConfig
from scalper_ai.rl.environment import TradingAction


def action_values() -> tuple[int, ...]:
    """Return the stable ordered action values used by the policy."""

    return tuple(int(action) for action in TradingAction)


def action_index_to_value(index: int) -> int:
    """Map a categorical action index into a trading action value."""

    values = action_values()
    if index < 0 or index >= len(values):
        raise ValueError("action index is out of range.")
    return values[index]


def action_value_to_index(action_value: int) -> int:
    """Map a trading action value into a categorical action index."""

    values = action_values()
    if action_value not in values:
        raise ValueError("action value must be one of -1, 0, 1.")
    return values.index(action_value)


@dataclass(frozen=True)
class PolicyOutput:
    """Policy forward-pass output."""

    logits: torch.Tensor
    probabilities: torch.Tensor


class TradingPolicyNetwork(nn.Module):
    """Small MLP policy for discrete trading actions."""

    def __init__(self, config: PolicyNetworkConfig) -> None:
        super().__init__()
        self.config = config
        self.network = nn.Sequential(
            nn.Linear(config.input_size, config.hidden_size),
            nn.ReLU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.hidden_size, config.hidden_size),
            nn.ReLU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.hidden_size, config.action_count),
        )

    def forward(self, observations: torch.Tensor) -> PolicyOutput:
        """Return logits and probabilities for batched observations."""

        if observations.ndim != 2:
            raise ValueError("observations must have shape [batch, features].")
        logits = self.network(observations)
        probabilities = torch.softmax(logits, dim=-1)
        return PolicyOutput(logits=logits, probabilities=probabilities)


def select_action(
    output: PolicyOutput,
    *,
    deterministic: bool = False,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Sample or greedily select policy actions from logits."""

    distribution = Categorical(logits=output.logits)
    if deterministic:
        action_indices = torch.argmax(output.logits, dim=-1)
    else:
        action_indices = distribution.sample()
    log_probabilities = distribution.log_prob(action_indices)
    return action_indices, log_probabilities


def action_indices_to_values(action_indices: Sequence[int] | torch.Tensor) -> list[int]:
    """Convert categorical action indices into trading action values."""

    if isinstance(action_indices, torch.Tensor):
        indices = action_indices.detach().cpu().tolist()
    else:
        indices = list(action_indices)
    return [action_index_to_value(int(index)) for index in indices]

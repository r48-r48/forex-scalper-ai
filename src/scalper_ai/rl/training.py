"""Policy training helpers for the offline trading RL environment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import torch
from torch import nn

from scalper_ai.rl.config import PolicyTrainingConfig
from scalper_ai.rl.environment import TradingEnvironment, TradingStepResult
from scalper_ai.rl.policy import TradingPolicyNetwork, action_index_to_value, select_action


@dataclass(frozen=True)
class PolicyTrainingBatch:
    """Batch of policy-gradient training tensors."""

    observations: torch.Tensor
    action_indices: torch.Tensor
    returns: torch.Tensor


@dataclass(frozen=True)
class PolicyTrainingResult:
    """Scalar metrics returned from one policy optimization step."""

    loss: float
    mean_return: float
    mean_entropy: float


@dataclass(frozen=True)
class EpisodeRollout:
    """Collected rollout data for one episode."""

    observations: np.ndarray
    action_indices: np.ndarray
    rewards: np.ndarray
    log_probabilities: np.ndarray
    total_reward: float
    total_pnl: float
    steps: int

    def to_training_batch(
        self,
        *,
        gamma: float = 0.99,
        normalize_returns: bool = True,
        device: Optional[torch.device | str] = None,
    ) -> PolicyTrainingBatch:
        """Convert rollout data into a policy-gradient batch."""

        returns = discounted_returns(self.rewards.tolist(), gamma=gamma, normalize=normalize_returns)
        return PolicyTrainingBatch(
            observations=torch.as_tensor(self.observations, dtype=torch.float32, device=device),
            action_indices=torch.as_tensor(self.action_indices, dtype=torch.int64, device=device),
            returns=returns.to(device=device),
        )


def discounted_returns(
    rewards: list[float],
    *,
    gamma: float,
    normalize: bool = True,
) -> torch.Tensor:
    """Return discounted returns for one reward trajectory."""

    if not rewards:
        return torch.zeros((0,), dtype=torch.float32)
    if not (0.0 < gamma <= 1.0):
        raise ValueError("gamma must be in the range (0.0, 1.0].")

    returns = []
    running_return = 0.0
    for reward in reversed(rewards):
        running_return = float(reward) + gamma * running_return
        returns.append(running_return)
    returns.reverse()

    tensor = torch.tensor(returns, dtype=torch.float32)
    if normalize and tensor.numel() > 1:
        tensor = (tensor - tensor.mean()) / (tensor.std(unbiased=False) + 1e-8)
    return tensor


def compute_reinforce_loss(
    policy: TradingPolicyNetwork,
    batch: PolicyTrainingBatch,
    *,
    entropy_coefficient: float = 0.0,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Compute REINFORCE loss and policy entropy for one batch."""

    output = policy(batch.observations)
    distribution = torch.distributions.Categorical(logits=output.logits)
    log_probabilities = distribution.log_prob(batch.action_indices)
    entropy = distribution.entropy()
    loss = -(log_probabilities * batch.returns).mean() - entropy_coefficient * entropy.mean()
    return loss, entropy


def train_policy_batch(
    policy: TradingPolicyNetwork,
    optimizer: torch.optim.Optimizer,
    batch: PolicyTrainingBatch,
    *,
    config: PolicyTrainingConfig | None = None,
) -> PolicyTrainingResult:
    """Run one optimization step of REINFORCE on a prebuilt batch."""

    resolved_config = config or PolicyTrainingConfig()
    optimizer.zero_grad(set_to_none=True)
    loss, entropy = compute_reinforce_loss(
        policy,
        batch,
        entropy_coefficient=resolved_config.entropy_coefficient,
    )
    loss.backward()
    if resolved_config.grad_clip_norm is not None:
        nn.utils.clip_grad_norm_(policy.parameters(), max_norm=resolved_config.grad_clip_norm)
    optimizer.step()
    return PolicyTrainingResult(
        loss=float(loss.detach().item()),
        mean_return=float(batch.returns.mean().detach().item()) if batch.returns.numel() > 0 else 0.0,
        mean_entropy=float(entropy.mean().detach().item()) if entropy.numel() > 0 else 0.0,
    )


@torch.inference_mode()
def rollout_policy_episode(
    environment: TradingEnvironment,
    policy: TradingPolicyNetwork,
    *,
    deterministic: bool = True,
    device: Optional[torch.device | str] = None,
) -> EpisodeRollout:
    """Roll out one full episode using the provided policy."""

    observation = environment.reset()
    observations: list[np.ndarray] = []
    action_indices: list[int] = []
    rewards: list[float] = []
    log_probabilities: list[float] = []
    total_reward = 0.0
    total_pnl = 0.0
    step_count = 0

    while True:
        observation_tensor = torch.as_tensor(
            observation.values.reshape(1, -1),
            dtype=torch.float32,
            device=device,
        )
        output = policy(observation_tensor)
        selected_indices, selected_log_probabilities = select_action(output, deterministic=deterministic)
        action_index = int(selected_indices.item())
        step_result = environment.step(action_index_to_value(action_index))

        observations.append(observation.values.copy())
        action_indices.append(action_index)
        rewards.append(step_result.reward)
        log_probabilities.append(float(selected_log_probabilities.item()))
        total_reward += step_result.reward
        total_pnl = float(step_result.info["cumulative_pnl"])
        step_count += 1

        if step_result.done:
            break
        observation = step_result.observation

    return EpisodeRollout(
        observations=np.asarray(observations, dtype=np.float32),
        action_indices=np.asarray(action_indices, dtype=np.int64),
        rewards=np.asarray(rewards, dtype=np.float32),
        log_probabilities=np.asarray(log_probabilities, dtype=np.float32),
        total_reward=total_reward,
        total_pnl=total_pnl,
        steps=step_count,
    )

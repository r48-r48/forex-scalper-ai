"""Configuration contracts for RL environments and policy training."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradingEnvironmentConfig:
    """Configuration for the offline trading RL environment."""

    price_column: str = "mid_price"
    timestamp_column: str = "available_timestamp"
    feature_columns: tuple[str, ...] | None = None
    position_size: float = 1.0
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    holding_cost_bps: float = 0.0
    reward_scale: float = 1.0
    include_position_in_observation: bool = True

    def __post_init__(self) -> None:
        if not self.price_column.strip():
            raise ValueError("price_column must be non-empty.")
        if not self.timestamp_column.strip():
            raise ValueError("timestamp_column must be non-empty.")
        if self.feature_columns is not None and not self.feature_columns:
            raise ValueError("feature_columns must not be empty when provided.")
        if self.position_size <= 0:
            raise ValueError("position_size must be greater than zero.")
        if self.spread_bps < 0:
            raise ValueError("spread_bps must be non-negative.")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative.")
        if self.holding_cost_bps < 0:
            raise ValueError("holding_cost_bps must be non-negative.")
        if self.reward_scale <= 0:
            raise ValueError("reward_scale must be greater than zero.")


@dataclass(frozen=True)
class PolicyNetworkConfig:
    """Configuration for a baseline categorical trading policy."""

    input_size: int
    hidden_size: int = 64
    action_count: int = 3
    dropout: float = 0.0

    def __post_init__(self) -> None:
        if self.input_size <= 0:
            raise ValueError("input_size must be greater than zero.")
        if self.hidden_size <= 0:
            raise ValueError("hidden_size must be greater than zero.")
        if self.action_count <= 1:
            raise ValueError("action_count must be greater than one.")
        if not (0.0 <= self.dropout < 1.0):
            raise ValueError("dropout must be in the range [0.0, 1.0).")


@dataclass(frozen=True)
class PolicyTrainingConfig:
    """Configuration for policy-gradient training helpers."""

    gamma: float = 0.99
    entropy_coefficient: float = 0.0
    normalize_returns: bool = True
    grad_clip_norm: float | None = None

    def __post_init__(self) -> None:
        if not (0.0 < self.gamma <= 1.0):
            raise ValueError("gamma must be in the range (0.0, 1.0].")
        if self.entropy_coefficient < 0:
            raise ValueError("entropy_coefficient must be non-negative.")
        if self.grad_clip_norm is not None and self.grad_clip_norm <= 0:
            raise ValueError("grad_clip_norm must be greater than zero when provided.")

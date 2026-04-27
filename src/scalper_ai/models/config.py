"""Configuration contracts for supervised signal models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ActivationName = Literal["relu", "gelu"]


@dataclass(frozen=True)
class TransformerSignalConfig:
    """Hyperparameters for the baseline transformer signal model."""

    input_size: int
    context_length: int
    model_dim: int = 128
    num_heads: int = 4
    num_layers: int = 3
    feedforward_dim: int = 256
    dropout: float = 0.1
    output_dim: int = 1
    activation: ActivationName = "gelu"
    layer_norm_eps: float = 1e-5

    def __post_init__(self) -> None:
        if self.input_size <= 0:
            raise ValueError("input_size must be greater than zero.")
        if self.context_length <= 0:
            raise ValueError("context_length must be greater than zero.")
        if self.model_dim <= 0:
            raise ValueError("model_dim must be greater than zero.")
        if self.num_heads <= 0:
            raise ValueError("num_heads must be greater than zero.")
        if self.model_dim % self.num_heads != 0:
            raise ValueError("model_dim must be divisible by num_heads.")
        if self.num_layers <= 0:
            raise ValueError("num_layers must be greater than zero.")
        if self.feedforward_dim <= 0:
            raise ValueError("feedforward_dim must be greater than zero.")
        if not (0.0 <= self.dropout < 1.0):
            raise ValueError("dropout must be in the range [0.0, 1.0).")
        if self.output_dim <= 0:
            raise ValueError("output_dim must be greater than zero.")
        if self.activation not in {"relu", "gelu"}:
            raise ValueError("activation must be one of: relu, gelu.")
        if self.layer_norm_eps <= 0:
            raise ValueError("layer_norm_eps must be greater than zero.")

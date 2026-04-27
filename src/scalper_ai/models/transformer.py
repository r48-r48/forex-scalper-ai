"""Baseline causal transformer for supervised signal prediction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
import torch
from torch import nn

from scalper_ai.models.config import TransformerSignalConfig
from scalper_ai.models.tensorizer import LaggedFeatureTensorizer, SignalModelBatch


@dataclass(frozen=True)
class SignalModelOutput:
    """Model output contract for supervised forecasting."""

    predictions: torch.Tensor
    encoded_sequence: torch.Tensor


class TransformerSignalModel(nn.Module):
    """Causal transformer encoder that predicts from lagged feature sequences."""

    def __init__(self, config: TransformerSignalConfig) -> None:
        super().__init__()
        self.config = config
        self.input_projection = nn.Linear(config.input_size, config.model_dim)
        self.input_norm = nn.LayerNorm(config.model_dim, eps=config.layer_norm_eps)
        self.position_embedding = nn.Parameter(torch.zeros(config.context_length, config.model_dim))

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=config.model_dim,
            nhead=config.num_heads,
            dim_feedforward=config.feedforward_dim,
            dropout=config.dropout,
            activation=config.activation,
            layer_norm_eps=config.layer_norm_eps,
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            encoder_layer=encoder_layer,
            num_layers=config.num_layers,
            enable_nested_tensor=False,
        )
        self.output_norm = nn.LayerNorm(config.model_dim, eps=config.layer_norm_eps)
        self.output_head = nn.Linear(config.model_dim, config.output_dim)
        self.register_buffer(
            "_causal_mask",
            causal_attention_mask(config.context_length),
            persistent=False,
        )
        self._reset_parameters()

    def forward(
        self,
        inputs: torch.Tensor,
        *,
        padding_mask: Optional[torch.Tensor] = None,
    ) -> SignalModelOutput:
        """Run a forward pass over [batch, time, feature] inputs."""

        if inputs.ndim != 3:
            raise ValueError("inputs must have shape [batch, time, feature].")
        batch_size, context_length, input_size = inputs.shape
        if context_length != self.config.context_length:
            raise ValueError(
                f"inputs time dimension {context_length} does not match context_length {self.config.context_length}."
            )
        if input_size != self.config.input_size:
            raise ValueError(
                f"inputs feature dimension {input_size} does not match input_size {self.config.input_size}."
            )
        if padding_mask is not None and padding_mask.shape != (batch_size, context_length):
            raise ValueError("padding_mask must have shape [batch, time].")

        encoded = self.input_projection(inputs)
        encoded = self.input_norm(encoded)
        encoded = encoded + self.position_embedding.unsqueeze(0)
        encoded = self.encoder(
            encoded,
            mask=self._causal_mask,
            src_key_padding_mask=padding_mask,
        )
        encoded = self.output_norm(encoded)
        predictions = self.output_head(encoded[:, -1, :])
        return SignalModelOutput(predictions=predictions, encoded_sequence=encoded)

    def _reset_parameters(self) -> None:
        nn.init.normal_(self.position_embedding, mean=0.0, std=0.02)


class TransformerSignalPredictor:
    """Thin inference wrapper for lagged feature frames and tensor batches."""

    def __init__(
        self,
        model: TransformerSignalModel,
        tensorizer: LaggedFeatureTensorizer,
        *,
        device: Optional[torch.device | str] = None,
    ) -> None:
        self._model = model
        self._tensorizer = tensorizer
        self._device = device

    @torch.inference_mode()
    def predict_frame(self, feature_frame: pd.DataFrame) -> SignalModelOutput:
        """Tensorize a feature frame and run model inference."""

        batch = self._tensorizer.build_batch(feature_frame, device=self._device)
        return self._model(batch.inputs)

    @torch.inference_mode()
    def predict_batch(self, batch: SignalModelBatch) -> SignalModelOutput:
        """Run inference on an already-tensorized batch."""

        return self._model(batch.inputs)


def causal_attention_mask(
    context_length: int,
    *,
    device: Optional[torch.device | str] = None,
) -> torch.Tensor:
    """Return an additive causal mask for transformer attention."""

    if context_length <= 0:
        raise ValueError("context_length must be greater than zero.")
    mask = torch.zeros((context_length, context_length), dtype=torch.float32, device=device)
    upper_triangle = torch.triu(torch.ones_like(mask, dtype=torch.bool), diagonal=1)
    mask.masked_fill_(upper_triangle, float("-inf"))
    return mask

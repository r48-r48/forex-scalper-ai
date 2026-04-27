"""Unit tests for the baseline transformer signal model."""

from __future__ import annotations

import torch

from scalper_ai.models.config import TransformerSignalConfig
from scalper_ai.models.transformer import TransformerSignalModel, causal_attention_mask


def test_causal_attention_mask_blocks_future_positions() -> None:
    mask = causal_attention_mask(4)

    assert mask.shape == (4, 4)
    assert torch.isinf(mask[0, 1])
    assert mask[1, 0] == 0
    assert mask[3, 3] == 0


def test_transformer_signal_model_forward_shape_and_determinism() -> None:
    torch.manual_seed(7)
    config = TransformerSignalConfig(
        input_size=3,
        context_length=5,
        model_dim=12,
        num_heads=3,
        num_layers=2,
        feedforward_dim=24,
        dropout=0.0,
        output_dim=2,
    )
    model = TransformerSignalModel(config)
    model.eval()
    inputs = torch.randn(4, 5, 3)

    first = model(inputs)
    second = model(inputs)

    assert first.predictions.shape == (4, 2)
    assert first.encoded_sequence.shape == (4, 5, 12)
    assert torch.allclose(first.predictions, second.predictions)

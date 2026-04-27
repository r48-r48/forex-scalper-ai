"""Integration tests for the dataset-to-model bridge."""

from __future__ import annotations

import pandas as pd
import torch

from scalper_ai.models.config import TransformerSignalConfig
from scalper_ai.models.tensorizer import LaggedFeatureTensorizer
from scalper_ai.models.transformer import TransformerSignalModel, TransformerSignalPredictor


def test_lagged_dataset_frame_flows_into_transformer_predictor() -> None:
    feature_frame = pd.DataFrame(
        {
            "lag_000__spread": [0.30, 0.40],
            "lag_000__mid_return": [0.03, 0.04],
            "lag_001__spread": [0.20, 0.30],
            "lag_001__mid_return": [0.02, 0.03],
            "lag_002__spread": [0.10, 0.20],
            "lag_002__mid_return": [0.01, 0.02],
        }
    )
    tensorizer = LaggedFeatureTensorizer(feature_frame.columns)
    model = TransformerSignalModel(
        TransformerSignalConfig(
            input_size=tensorizer.input_size,
            context_length=tensorizer.context_length,
            model_dim=8,
            num_heads=2,
            num_layers=2,
            feedforward_dim=16,
            dropout=0.0,
            output_dim=1,
        )
    )
    predictor = TransformerSignalPredictor(model, tensorizer)

    output = predictor.predict_frame(feature_frame)

    assert output.predictions.shape == (2, 1)
    assert output.encoded_sequence.shape == (2, 3, 8)
    assert torch.isfinite(output.predictions).all()

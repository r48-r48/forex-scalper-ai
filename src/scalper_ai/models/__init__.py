"""Supervised forecasting models and tensorization helpers."""

from scalper_ai.models.baseline_filter import (
    SupervisedBaselineFilterConfig,
    SupervisedBaselineFilterModel,
    fit_supervised_baseline_filter,
    target_directions,
)
from scalper_ai.models.config import TransformerSignalConfig
from scalper_ai.models.tensorizer import LaggedFeatureTensorizer, SignalModelBatch
from scalper_ai.models.transformer import (
    SignalModelOutput,
    TransformerSignalModel,
    TransformerSignalPredictor,
    causal_attention_mask,
)

__all__ = [
    "LaggedFeatureTensorizer",
    "SignalModelBatch",
    "SupervisedBaselineFilterConfig",
    "SupervisedBaselineFilterModel",
    "SignalModelOutput",
    "TransformerSignalConfig",
    "TransformerSignalModel",
    "TransformerSignalPredictor",
    "causal_attention_mask",
    "fit_supervised_baseline_filter",
    "target_directions",
]

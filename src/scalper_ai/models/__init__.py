"""Supervised forecasting models and tensorization helpers."""

from scalper_ai.models.baseline_filter import (
    SupervisedBaselineFilterConfig,
    SupervisedBaselineFilterModel,
    fit_supervised_baseline_filter,
    target_directions,
)
from scalper_ai.models.bundle import (
    ModelBundleArtifact,
    ModelBundleMetadata,
    ModelTargetSpec,
    TrainingDataWindow,
    compute_feature_contract_hash,
    load_model_bundle_metadata,
    save_model_bundle_metadata,
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
    "ModelBundleArtifact",
    "ModelBundleMetadata",
    "ModelTargetSpec",
    "SignalModelBatch",
    "SupervisedBaselineFilterConfig",
    "SupervisedBaselineFilterModel",
    "SignalModelOutput",
    "TrainingDataWindow",
    "TransformerSignalConfig",
    "TransformerSignalModel",
    "TransformerSignalPredictor",
    "causal_attention_mask",
    "compute_feature_contract_hash",
    "fit_supervised_baseline_filter",
    "load_model_bundle_metadata",
    "save_model_bundle_metadata",
    "target_directions",
]

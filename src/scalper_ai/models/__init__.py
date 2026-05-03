"""Supervised forecasting models and tensorization helpers."""

from scalper_ai.models.baseline_filter import (
    SupervisedBaselineFilterConfig,
    SupervisedBaselineFilterModel,
    fit_supervised_baseline_filter,
    load_supervised_baseline_filter_model,
    save_supervised_baseline_filter_model,
    supervised_baseline_filter_model_from_dict,
    supervised_baseline_filter_model_to_dict,
    target_directions,
)
from scalper_ai.models.bundle import (
    ModelBundleArtifact,
    ModelBundleMetadata,
    ModelTargetSpec,
    TrainingDataWindow,
    compute_feature_contract_hash,
    hash_file_sha256,
    load_model_bundle_metadata,
    save_model_bundle_metadata,
)
from scalper_ai.models.config import TransformerSignalConfig
from scalper_ai.models.runtime import (
    BASELINE_FILTER_MODEL_TYPE,
    BaselineFilterInferencePackage,
    BaselineFilterSignal,
    load_baseline_filter_inference_package,
)
from scalper_ai.models.tensorizer import LaggedFeatureTensorizer, SignalModelBatch
from scalper_ai.models.transformer import (
    SignalModelOutput,
    TransformerSignalModel,
    TransformerSignalPredictor,
    causal_attention_mask,
)

__all__ = [
    "BASELINE_FILTER_MODEL_TYPE",
    "BaselineFilterInferencePackage",
    "BaselineFilterSignal",
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
    "hash_file_sha256",
    "load_baseline_filter_inference_package",
    "load_model_bundle_metadata",
    "load_supervised_baseline_filter_model",
    "save_model_bundle_metadata",
    "save_supervised_baseline_filter_model",
    "supervised_baseline_filter_model_from_dict",
    "supervised_baseline_filter_model_to_dict",
    "target_directions",
]

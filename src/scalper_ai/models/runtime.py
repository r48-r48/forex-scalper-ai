"""Runtime inference package loading for promoted model bundles."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import torch

from scalper_ai.models.baseline_filter import (
    SupervisedBaselineFilterModel,
    load_supervised_baseline_filter_model,
)
from scalper_ai.models.bundle import (
    ModelBundleArtifact,
    ModelBundleMetadata,
    hash_file_sha256,
    load_model_bundle_metadata,
)
from scalper_ai.models.config import ActivationName, TransformerSignalConfig
from scalper_ai.models.tensorizer import LaggedFeatureTensorizer
from scalper_ai.models.transformer import TransformerSignalModel

BASELINE_FILTER_MODEL_TYPE = "supervised_baseline_filter"
TRANSFORMER_SIGNAL_MODEL_TYPE = "transformer_signal"
_TRANSFORMER_MODEL_FORMAT_VERSION = 1
_SCALER_FORMAT_VERSION = 1


@dataclass(frozen=True)
class BaselineFilterSignal:
    """Single-row runtime prediction emitted from a supervised baseline bundle."""

    model_id: str
    schema_hash: str
    score: float
    prediction: int
    symbol: str | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class BaselineFilterInferencePackage:
    """Loaded supervised baseline filter with its production bundle contract."""

    metadata: ModelBundleMetadata
    model: SupervisedBaselineFilterModel

    def __post_init__(self) -> None:
        if self.metadata.model_type != BASELINE_FILTER_MODEL_TYPE:
            raise ValueError(
                f"metadata.model_type must be {BASELINE_FILTER_MODEL_TYPE}."
            )
        if self.model.feature_columns != self.metadata.feature_columns:
            raise ValueError("Model feature columns must match bundle metadata.")

    def score_frame(self, feature_frame: pd.DataFrame) -> pd.Series:
        """Return signed model scores after enforcing the bundle feature contract."""

        _validate_feature_frame_contract(
            feature_frame,
            feature_columns=self.metadata.feature_columns,
        )
        return self.model.score_frame(feature_frame)

    def predict_frame(self, feature_frame: pd.DataFrame) -> pd.Series:
        """Return directional runtime predictions {-1, 0, 1}."""

        _validate_feature_frame_contract(
            feature_frame,
            feature_columns=self.metadata.feature_columns,
        )
        return self.model.predict_frame(feature_frame)

    def predict_latest(
        self,
        feature_frame: pd.DataFrame,
        *,
        symbol: str | None = None,
        timestamp: datetime | pd.Timestamp | None = None,
    ) -> BaselineFilterSignal:
        """Return the last-row signal from a runtime feature frame."""

        if feature_frame.empty:
            raise ValueError("feature_frame must contain at least one row.")
        latest_frame = feature_frame.tail(1)
        score = float(self.score_frame(latest_frame).iloc[-1])
        prediction = int(self.predict_frame(latest_frame).iloc[-1])
        return BaselineFilterSignal(
            model_id=self.metadata.model_id,
            schema_hash=self.metadata.schema_hash,
            score=score,
            prediction=prediction,
            symbol=None if symbol is None else symbol.strip() or None,
            timestamp=_coerce_optional_utc_timestamp(timestamp),
        )


@dataclass(frozen=True)
class TransformerSignal:
    """Single-row runtime prediction emitted from a transformer signal bundle."""

    model_id: str
    schema_hash: str
    score: float
    symbol: str | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class TransformerFeatureScaler:
    """Standard mean/scale transformer feature preprocessing contract."""

    feature_columns: tuple[str, ...]
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]

    def __post_init__(self) -> None:
        expected = len(self.feature_columns)
        if expected == 0:
            raise ValueError("feature_columns must not be empty.")
        if len(self.feature_means) != expected:
            raise ValueError("feature_means length must match feature_columns.")
        if len(self.feature_scales) != expected:
            raise ValueError("feature_scales length must match feature_columns.")
        if any(scale <= 0 for scale in self.feature_scales):
            raise ValueError("feature_scales must be positive.")
        values = (*self.feature_means, *self.feature_scales)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("feature scaler values must be finite.")

    def transform_frame(self, feature_frame: pd.DataFrame) -> pd.DataFrame:
        """Return a standardized lagged feature frame."""

        _validate_feature_frame_contract(
            feature_frame,
            feature_columns=self.feature_columns,
        )
        values = feature_frame.loc[:, list(self.feature_columns)].to_numpy(
            dtype=float,
            copy=True,
        )
        if not np.isfinite(values).all():
            raise ValueError("Feature frame contains non-finite values.")
        means = np.asarray(self.feature_means, dtype=float)
        scales = np.asarray(self.feature_scales, dtype=float)
        scaled = (values - means) / scales
        return pd.DataFrame(scaled, columns=self.feature_columns, index=feature_frame.index)


@dataclass(frozen=True)
class TransformerInferencePackage:
    """Loaded transformer signal bundle with metadata and preprocessing."""

    metadata: ModelBundleMetadata
    model: TransformerSignalModel
    tensorizer: LaggedFeatureTensorizer
    scaler: TransformerFeatureScaler

    def __post_init__(self) -> None:
        if self.metadata.model_type != TRANSFORMER_SIGNAL_MODEL_TYPE:
            raise ValueError(
                f"metadata.model_type must be {TRANSFORMER_SIGNAL_MODEL_TYPE}."
            )
        if self.scaler.feature_columns != self.metadata.feature_columns:
            raise ValueError("Scaler feature columns must match bundle metadata.")
        if self.model.config.input_size != self.tensorizer.input_size:
            raise ValueError("Model input_size must match tensorizer input_size.")
        if self.model.config.context_length != self.tensorizer.context_length:
            raise ValueError("Model context_length must match tensorizer context_length.")

    @torch.inference_mode()
    def score_frame(self, feature_frame: pd.DataFrame) -> pd.Series:
        """Return continuous transformer predictions after contract validation."""

        _validate_feature_frame_contract(
            feature_frame,
            feature_columns=self.metadata.feature_columns,
        )
        scaled_frame = self.scaler.transform_frame(feature_frame)
        batch = self.tensorizer.build_batch(scaled_frame)
        self.model.eval()
        output = self.model(batch.inputs)
        values = output.predictions.detach().cpu().numpy().reshape(-1)
        return pd.Series(values, index=feature_frame.index, name="score")

    def predict_latest(
        self,
        feature_frame: pd.DataFrame,
        *,
        symbol: str | None = None,
        timestamp: datetime | pd.Timestamp | None = None,
    ) -> TransformerSignal:
        """Return the last-row transformer signal from a runtime feature frame."""

        if feature_frame.empty:
            raise ValueError("feature_frame must contain at least one row.")
        latest_frame = feature_frame.tail(1)
        score = float(self.score_frame(latest_frame).iloc[-1])
        return TransformerSignal(
            model_id=self.metadata.model_id,
            schema_hash=self.metadata.schema_hash,
            score=score,
            symbol=None if symbol is None else symbol.strip() or None,
            timestamp=_coerce_optional_utc_timestamp(timestamp),
        )


def load_baseline_filter_inference_package(
    bundle_path: Path,
) -> BaselineFilterInferencePackage:
    """Load a supervised baseline filter bundle and verify referenced artifacts."""

    metadata_path = bundle_path / "metadata.json" if bundle_path.is_dir() else bundle_path
    metadata = load_model_bundle_metadata(metadata_path)
    if metadata.model_type != BASELINE_FILTER_MODEL_TYPE:
        raise ValueError(
            "Unsupported model_type for baseline filter runtime: "
            f"{metadata.model_type}"
        )

    bundle_root = metadata_path.parent
    _verify_artifact(bundle_root, metadata.scaler_artifact)
    model_path = _verify_artifact(bundle_root, metadata.model_artifact)
    model = load_supervised_baseline_filter_model(model_path)
    return BaselineFilterInferencePackage(metadata=metadata, model=model)


def load_transformer_inference_package(
    bundle_path: Path,
    *,
    device: torch.device | str | None = None,
) -> TransformerInferencePackage:
    """Load a transformer signal bundle and verify referenced artifacts."""

    metadata_path = bundle_path / "metadata.json" if bundle_path.is_dir() else bundle_path
    metadata = load_model_bundle_metadata(metadata_path)
    if metadata.model_type != TRANSFORMER_SIGNAL_MODEL_TYPE:
        raise ValueError(
            "Unsupported model_type for transformer runtime: "
            f"{metadata.model_type}"
        )

    bundle_root = metadata_path.parent
    scaler_path = _verify_artifact(bundle_root, metadata.scaler_artifact)
    model_path = _verify_artifact(bundle_root, metadata.model_artifact)
    scaler = _load_transformer_feature_scaler(scaler_path)
    tensorizer = LaggedFeatureTensorizer(metadata.feature_columns)
    model = _load_transformer_model_artifact(model_path, device=device)
    return TransformerInferencePackage(
        metadata=metadata,
        model=model,
        tensorizer=tensorizer,
        scaler=scaler,
    )


def _verify_artifact(bundle_root: Path, artifact: ModelBundleArtifact) -> Path:
    path = Path(artifact.path)
    if not path.is_absolute():
        path = bundle_root / path
    if not path.is_file():
        raise ValueError(f"Bundle artifact does not exist: {artifact.path}")
    if artifact.sha256 is not None:
        actual_hash = hash_file_sha256(path)
        if actual_hash != artifact.sha256:
            raise ValueError(f"Bundle artifact hash mismatch: {artifact.path}")
    return path


def _load_transformer_feature_scaler(path: Path) -> TransformerFeatureScaler:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Transformer scaler JSON must be an object.")
    version = payload.get("scaler_format_version")
    if version != _SCALER_FORMAT_VERSION:
        raise ValueError(f"scaler_format_version must be {_SCALER_FORMAT_VERSION}.")
    scaler_type = payload.get("scaler_type")
    if scaler_type != "standard_mean_scale":
        raise ValueError("scaler_type must be standard_mean_scale.")
    return TransformerFeatureScaler(
        feature_columns=_required_text_tuple(payload, "feature_columns"),
        feature_means=_required_float_tuple(payload, "feature_means"),
        feature_scales=_required_float_tuple(payload, "feature_scales"),
    )


def _load_transformer_model_artifact(
    path: Path,
    *,
    device: torch.device | str | None,
) -> TransformerSignalModel:
    payload = torch.load(path, map_location=device, weights_only=True)
    if not isinstance(payload, Mapping):
        raise ValueError("Transformer model artifact must be a mapping.")
    version = payload.get("model_format_version")
    if version != _TRANSFORMER_MODEL_FORMAT_VERSION:
        raise ValueError(
            f"model_format_version must be {_TRANSFORMER_MODEL_FORMAT_VERSION}."
        )
    model_type = payload.get("model_type")
    if model_type != TRANSFORMER_SIGNAL_MODEL_TYPE:
        raise ValueError(f"model_type must be {TRANSFORMER_SIGNAL_MODEL_TYPE}.")
    config = _transformer_config_from_payload(payload.get("config"))
    state_dict = payload.get("state_dict")
    if not isinstance(state_dict, Mapping):
        raise ValueError("state_dict must be a mapping.")
    model = TransformerSignalModel(config)
    model.load_state_dict(cast(Mapping[str, Any], state_dict))
    model.eval()
    return model


def _transformer_config_from_payload(payload: object) -> TransformerSignalConfig:
    if not isinstance(payload, Mapping):
        raise ValueError("Transformer config must be an object.")
    return TransformerSignalConfig(
        input_size=_required_int(payload, "input_size"),
        context_length=_required_int(payload, "context_length"),
        model_dim=_required_int(payload, "model_dim"),
        num_heads=_required_int(payload, "num_heads"),
        num_layers=_required_int(payload, "num_layers"),
        feedforward_dim=_required_int(payload, "feedforward_dim"),
        dropout=_required_float(payload, "dropout"),
        output_dim=_required_int(payload, "output_dim"),
        activation=_required_activation(payload, "activation"),
        layer_norm_eps=_required_float(payload, "layer_norm_eps"),
    )


def _validate_feature_frame_contract(
    feature_frame: pd.DataFrame,
    *,
    feature_columns: tuple[str, ...],
) -> None:
    missing = [column for column in feature_columns if column not in feature_frame.columns]
    if missing:
        raise ValueError(f"Feature frame is missing required columns: {', '.join(missing)}")


def _required_text_tuple(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty list of strings.")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key} must contain non-empty strings.")
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{key} must not contain duplicates.")
    return tuple(normalized)


def _required_float_tuple(payload: Mapping[str, object], key: str) -> tuple[float, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty list of numbers.")
    return tuple(_required_finite_float(item, key) for item in value)


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    return value


def _required_float(payload: Mapping[str, object], key: str) -> float:
    if key not in payload:
        raise ValueError(f"{key} is required.")
    return _required_finite_float(payload[key], key)


def _required_finite_float(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be numeric.")
    resolved = float(value)
    if not math.isfinite(resolved):
        raise ValueError(f"{key} must be finite.")
    return resolved


def _required_activation(payload: Mapping[str, object], key: str) -> ActivationName:
    value = payload.get(key)
    if value not in {"relu", "gelu"}:
        raise ValueError("activation must be one of: relu, gelu.")
    return cast(ActivationName, value)


def _coerce_optional_utc_timestamp(
    timestamp: datetime | pd.Timestamp | None,
) -> datetime | None:
    if timestamp is None:
        return None
    resolved = pd.Timestamp(timestamp)
    if pd.isna(resolved):
        raise ValueError("timestamp must be a valid timestamp.")
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware.")
    normalized = cast(datetime, resolved.tz_convert("UTC").to_pydatetime())
    return normalized.astimezone(UTC)

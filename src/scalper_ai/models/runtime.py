"""Runtime inference package loading for promoted model bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pandas as pd

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

BASELINE_FILTER_MODEL_TYPE = "supervised_baseline_filter"


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


def _validate_feature_frame_contract(
    feature_frame: pd.DataFrame,
    *,
    feature_columns: tuple[str, ...],
) -> None:
    missing = [column for column in feature_columns if column not in feature_frame.columns]
    if missing:
        raise ValueError(f"Feature frame is missing required columns: {', '.join(missing)}")


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

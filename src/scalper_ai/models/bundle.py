"""Production model bundle metadata contracts and JSON persistence helpers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias, cast

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]

_BUNDLE_FORMAT_VERSION = 1
_HASH_ALGORITHM = "sha256"


@dataclass(frozen=True)
class ModelBundleArtifact:
    """A sidecar reference to a persisted artifact used by the model bundle."""

    name: str
    path: str
    sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, "artifact name"))
        object.__setattr__(self, "path", _require_text(self.path, "artifact path"))
        if self.sha256 is not None:
            object.__setattr__(self, "sha256", _validate_sha256(self.sha256, "sha256"))

    def to_dict(self) -> JsonObject:
        """Return a JSON-friendly artifact reference."""

        payload: JsonObject = {
            "name": self.name,
            "path": self.path,
        }
        if self.sha256 is not None:
            payload["sha256"] = self.sha256
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ModelBundleArtifact:
        """Restore an artifact reference from a metadata JSON mapping."""

        sha256 = payload.get("sha256")
        if sha256 is not None and not isinstance(sha256, str):
            raise ValueError("sha256 must be a string when provided.")
        return cls(
            name=_require_mapping_text(payload, "name"),
            path=_require_mapping_text(payload, "path"),
            sha256=sha256,
        )


@dataclass(frozen=True)
class ModelTargetSpec:
    """Leakage-safe target definition captured with a production model bundle."""

    name: str
    target_type: str
    horizon: str
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, "target name"))
        object.__setattr__(
            self,
            "target_type",
            _require_text(self.target_type, "target type"),
        )
        object.__setattr__(self, "horizon", _require_text(self.horizon, "target horizon"))
        object.__setattr__(
            self,
            "parameters",
            _coerce_json_mapping(self.parameters, "target parameters"),
        )

    def to_dict(self) -> JsonObject:
        """Return a JSON-friendly target specification."""

        return {
            "name": self.name,
            "target_type": self.target_type,
            "horizon": self.horizon,
            "parameters": dict(self.parameters),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ModelTargetSpec:
        """Restore a target specification from a metadata JSON mapping."""

        return cls(
            name=_require_mapping_text(payload, "name"),
            target_type=_require_mapping_text(payload, "target_type"),
            horizon=_require_mapping_text(payload, "horizon"),
            parameters=_optional_json_mapping(payload, "parameters"),
        )


@dataclass(frozen=True)
class TrainingDataWindow:
    """Training data provenance and timestamp window captured for promotion."""

    dataset_id: str
    symbols: tuple[str, ...]
    window_start: datetime
    window_end: datetime
    row_count: int
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_id", _require_text(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "symbols", _non_empty_text_tuple(self.symbols, "symbols"))
        start = _normalize_utc(self.window_start, "window_start")
        end = _normalize_utc(self.window_end, "window_end")
        if end < start:
            raise ValueError("window_end must be greater than or equal to window_start.")
        object.__setattr__(self, "window_start", start)
        object.__setattr__(self, "window_end", end)
        if self.row_count <= 0:
            raise ValueError("row_count must be greater than zero.")
        object.__setattr__(
            self,
            "metadata",
            _coerce_json_mapping(self.metadata, "training data metadata"),
        )

    def to_dict(self) -> JsonObject:
        """Return a JSON-friendly training data window."""

        return {
            "dataset_id": self.dataset_id,
            "symbols": list(self.symbols),
            "window_start": _format_utc(self.window_start),
            "window_end": _format_utc(self.window_end),
            "row_count": self.row_count,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> TrainingDataWindow:
        """Restore a training window from a metadata JSON mapping."""

        return cls(
            dataset_id=_require_mapping_text(payload, "dataset_id"),
            symbols=_required_text_sequence(payload, "symbols"),
            window_start=_parse_datetime(
                _require_mapping_text(payload, "window_start"),
                "window_start",
            ),
            window_end=_parse_datetime(_require_mapping_text(payload, "window_end"), "window_end"),
            row_count=_require_positive_int(payload, "row_count"),
            metadata=_optional_json_mapping(payload, "metadata"),
        )


@dataclass(frozen=True)
class ModelBundleMetadata:
    """Production sidecar metadata for a model, scaler, and feature contract."""

    model_id: str
    model_type: str
    trained_at: datetime
    feature_columns: tuple[str, ...]
    target_spec: ModelTargetSpec
    scaler_artifact: ModelBundleArtifact
    model_artifact: ModelBundleArtifact
    schema_hash: str
    metrics: Mapping[str, float]
    training_data: TrainingDataWindow
    bundle_format_version: int = _BUNDLE_FORMAT_VERSION
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _require_text(self.model_id, "model_id"))
        object.__setattr__(self, "model_type", _require_text(self.model_type, "model_type"))
        object.__setattr__(self, "trained_at", _normalize_utc(self.trained_at, "trained_at"))
        object.__setattr__(
            self,
            "feature_columns",
            _non_empty_text_tuple(self.feature_columns, "feature_columns"),
        )
        if self.bundle_format_version != _BUNDLE_FORMAT_VERSION:
            raise ValueError(
                f"bundle_format_version must be {_BUNDLE_FORMAT_VERSION} for this contract."
            )
        expected_hash = compute_feature_contract_hash(
            self.feature_columns,
            target_spec=self.target_spec,
        )
        if self.schema_hash != expected_hash:
            raise ValueError("schema_hash must match the deterministic feature contract hash.")
        object.__setattr__(self, "metrics", _coerce_metric_mapping(self.metrics))
        object.__setattr__(self, "metadata", _coerce_json_mapping(self.metadata, "metadata"))

    def to_dict(self) -> JsonObject:
        """Return a JSON-friendly metadata payload."""

        return {
            "bundle_format_version": self.bundle_format_version,
            "model_id": self.model_id,
            "model_type": self.model_type,
            "trained_at": _format_utc(self.trained_at),
            "feature_columns": list(self.feature_columns),
            "target_spec": self.target_spec.to_dict(),
            "scaler_artifact": self.scaler_artifact.to_dict(),
            "model_artifact": self.model_artifact.to_dict(),
            "schema_hash": self.schema_hash,
            "hash_algorithm": _HASH_ALGORITHM,
            "metrics": dict(self.metrics),
            "training_data": self.training_data.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ModelBundleMetadata:
        """Restore model bundle metadata from a JSON mapping."""

        hash_algorithm = payload.get("hash_algorithm")
        if hash_algorithm is not None and hash_algorithm != _HASH_ALGORITHM:
            raise ValueError(f"hash_algorithm must be {_HASH_ALGORITHM}.")
        return cls(
            bundle_format_version=_optional_format_version(payload),
            model_id=_require_mapping_text(payload, "model_id"),
            model_type=_require_mapping_text(payload, "model_type"),
            trained_at=_parse_datetime(_require_mapping_text(payload, "trained_at"), "trained_at"),
            feature_columns=_required_text_sequence(payload, "feature_columns"),
            target_spec=ModelTargetSpec.from_dict(_required_mapping(payload, "target_spec")),
            scaler_artifact=ModelBundleArtifact.from_dict(
                _required_mapping(payload, "scaler_artifact")
            ),
            model_artifact=ModelBundleArtifact.from_dict(
                _required_mapping(payload, "model_artifact")
            ),
            schema_hash=_require_mapping_text(payload, "schema_hash"),
            metrics=_required_metric_mapping(payload, "metrics"),
            training_data=TrainingDataWindow.from_dict(_required_mapping(payload, "training_data")),
            metadata=_optional_json_mapping(payload, "metadata"),
        )


def compute_feature_contract_hash(
    feature_columns: Sequence[str],
    *,
    target_spec: ModelTargetSpec,
) -> str:
    """Return a stable SHA-256 hash for the ordered feature and target contract."""

    normalized_columns = _non_empty_text_tuple(feature_columns, "feature_columns")
    payload: JsonObject = {
        "feature_columns": list(normalized_columns),
        "target_spec": target_spec.to_dict(),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def save_model_bundle_metadata(metadata: ModelBundleMetadata, path: Path) -> Path:
    """Persist model bundle metadata JSON with a same-directory atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        metadata.to_dict(),
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    )
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(serialized)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return path


def load_model_bundle_metadata(path: Path) -> ModelBundleMetadata:
    """Load and validate model bundle metadata JSON."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Model bundle metadata JSON must be an object.")
    return ModelBundleMetadata.from_dict(cast(Mapping[str, object], payload))


def _format_utc(timestamp: datetime) -> str:
    normalized = _normalize_utc(timestamp, "timestamp")
    return normalized.isoformat().replace("+00:00", "Z")


def _normalize_utc(timestamp: datetime, field_name: str) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return timestamp.astimezone(UTC)


def _parse_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime.") from exc
    return _normalize_utc(parsed, field_name)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _require_mapping_text(payload: Mapping[str, object], key: str) -> str:
    if key not in payload:
        raise ValueError(f"{key} is required.")
    return _require_text(payload[key], key)


def _required_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object.")
    return cast(Mapping[str, object], value)


def _non_empty_text_tuple(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_require_text(value, field_name) for value in values)
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates.")
    return normalized


def _required_text_sequence(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError(f"{key} must be a non-empty list of strings.")
    return _non_empty_text_tuple(cast(Sequence[str], value), key)


def _require_positive_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    if value <= 0:
        raise ValueError(f"{key} must be greater than zero.")
    return value


def _optional_format_version(payload: Mapping[str, object]) -> int:
    value = payload.get("bundle_format_version", _BUNDLE_FORMAT_VERSION)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("bundle_format_version must be an integer.")
    return value


def _coerce_metric_mapping(metrics: Mapping[str, float]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for key, value in metrics.items():
        metric_name = _require_text(key, "metric name")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"metric {metric_name} must be numeric.")
        metric_value = float(value)
        if not math.isfinite(metric_value):
            raise ValueError(f"metric {metric_name} must be finite.")
        normalized[metric_name] = metric_value
    return normalized


def _required_metric_mapping(payload: Mapping[str, object], key: str) -> dict[str, float]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object.")
    normalized: dict[str, float] = {}
    for metric_name, metric_value in value.items():
        normalized[_require_text(metric_name, "metric name")] = _coerce_metric_value(
            metric_value,
            str(metric_name),
        )
    return normalized


def _coerce_metric_value(value: object, metric_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"metric {metric_name} must be numeric.")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"metric {metric_name} must be finite.")
    return normalized


def _optional_json_mapping(payload: Mapping[str, object], key: str) -> dict[str, JsonValue]:
    value = payload.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object.")
    return _coerce_json_mapping(cast(Mapping[str, object], value), key)


def _coerce_json_mapping(
    payload: Mapping[str, object],
    field_name: str,
) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{field_name} keys must be non-empty strings.")
        normalized[key] = _coerce_json_value(value, f"{field_name}.{key}")
    return normalized


def _coerce_json_value(value: object, field_name: str) -> JsonValue:
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int | float):
        if not math.isfinite(float(value)):
            raise ValueError(f"{field_name} must be finite when numeric.")
        return value
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_coerce_json_value(item, field_name) for item in value]
    if isinstance(value, Mapping):
        return _coerce_json_mapping(cast(Mapping[str, object], value), field_name)
    raise ValueError(f"{field_name} must be JSON-serializable.")


def _validate_sha256(value: str, field_name: str) -> str:
    normalized = _require_text(value, field_name).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest.")
    return normalized

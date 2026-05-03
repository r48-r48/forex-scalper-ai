"""Train and export a promoted transformer signal bundle."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd
import torch
from torch import nn

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"
for import_path in (SCRIPT_ROOT, SRC_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from cli_utils import load_frame, write_json
from run_walk_forward import supervised_dataset_from_frame

from scalper_ai.data.datasets import SupervisedDataset
from scalper_ai.data.labels import (
    TARGET_COLUMN,
    TARGET_END_EVENT_TIMESTAMP_COLUMN,
    TARGET_END_TIMESTAMP_COLUMN,
)
from scalper_ai.models import (
    ModelBundleArtifact,
    ModelBundleMetadata,
    ModelTargetSpec,
    TrainingDataWindow,
    TransformerSignalConfig,
    TransformerSignalModel,
    compute_feature_contract_hash,
    hash_file_sha256,
    save_model_bundle_metadata,
)
from scalper_ai.models.bundle import JsonValue
from scalper_ai.models.runtime import TRANSFORMER_SIGNAL_MODEL_TYPE
from scalper_ai.models.tensorizer import LaggedFeatureTensorizer

_MODEL_FORMAT_VERSION = 1
_SCALER_FORMAT_VERSION = 1


class _FeatureScaler(NamedTuple):
    feature_columns: tuple[str, ...]
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]


def train_transformer_cli(
    *,
    input_path: Path,
    output_dir: Path,
    model_id: str,
    training_start: str | datetime | pd.Timestamp | None = None,
    training_end: str | datetime | pd.Timestamp | None = None,
    input_is_train_only: bool = False,
    dataset_id: str | None = None,
    target_horizon: str = "unknown",
    validation_size: int = 0,
    validation_fraction: float = 0.2,
    epochs: int = 5,
    batch_size: int = 32,
    learning_rate: float = 1e-3,
    weight_decay: float = 0.0,
    model_dim: int = 64,
    num_heads: int = 4,
    num_layers: int = 2,
    feedforward_dim: int = 128,
    dropout: float = 0.0,
    seed: int = 7,
    min_scale: float = 1e-12,
    report_output_path: Path | None = None,
) -> dict[str, object]:
    """Fit a transformer on an explicit training window and export a bundle."""

    _validate_training_arguments(
        model_id=model_id,
        validation_size=validation_size,
        validation_fraction=validation_fraction,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        weight_decay=weight_decay,
        seed=seed,
        min_scale=min_scale,
    )
    dataset_frame = load_frame(input_path)
    training_frame = _select_training_frame(
        dataset_frame,
        training_start=training_start,
        training_end=training_end,
        input_is_train_only=input_is_train_only,
    )
    dataset = supervised_dataset_from_frame(training_frame)
    train_dataset, validation_dataset = _split_train_validation_dataset(
        dataset,
        validation_size=validation_size,
        validation_fraction=validation_fraction,
    )
    tensorizer = LaggedFeatureTensorizer(dataset.feature_columns)
    scaler = _fit_feature_scaler(
        train_dataset.features,
        feature_columns=dataset.feature_columns,
        min_scale=min_scale,
    )
    transformer_config = TransformerSignalConfig(
        input_size=tensorizer.input_size,
        context_length=tensorizer.context_length,
        model_dim=model_dim,
        num_heads=num_heads,
        num_layers=num_layers,
        feedforward_dim=feedforward_dim,
        dropout=dropout,
        output_dim=1,
    )
    model, training_loss = _fit_transformer_model(
        train_dataset=train_dataset,
        validation_dataset=validation_dataset,
        tensorizer=tensorizer,
        scaler=scaler,
        config=transformer_config,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        weight_decay=weight_decay,
        seed=seed,
    )
    metrics = _training_metrics(
        model=model,
        tensorizer=tensorizer,
        scaler=scaler,
        train_dataset=train_dataset,
        validation_dataset=validation_dataset,
        final_training_loss=training_loss,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.pt"
    scaler_path = output_dir / "scaler.json"
    metadata_path = output_dir / "metadata.json"
    resolved_report_path = report_output_path or output_dir / "training-report.json"

    _save_transformer_model_artifact(model, model_path)
    write_json(_scaler_payload(scaler), scaler_path)

    target_spec = ModelTargetSpec(
        name=TARGET_COLUMN,
        target_type="regression_forecast",
        horizon=target_horizon,
        parameters={
            "loss": "mse",
            "validation_size": validation_size,
            "validation_fraction": validation_fraction,
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "weight_decay": weight_decay,
            "min_scale": min_scale,
        },
    )
    metadata = _build_bundle_metadata(
        model_id=model_id.strip(),
        dataset=dataset,
        input_path=input_path,
        input_rows=len(dataset_frame),
        input_is_train_only=input_is_train_only,
        training_start=training_start,
        training_end=training_end,
        dataset_id=dataset_id or input_path.stem,
        target_spec=target_spec,
        model_artifact=ModelBundleArtifact(
            name="transformer-signal-model",
            path=model_path.name,
            sha256=hash_file_sha256(model_path),
        ),
        scaler_artifact=ModelBundleArtifact(
            name="transformer-feature-scaler",
            path=scaler_path.name,
            sha256=hash_file_sha256(scaler_path),
        ),
        metrics=metrics,
        transformer_config=transformer_config,
        seed=seed,
    )
    save_model_bundle_metadata(metadata, metadata_path)

    payload: dict[str, object] = {
        "input_path": str(input_path),
        "input_rows": len(dataset_frame),
        "training_rows": len(dataset),
        "fit_rows": len(train_dataset),
        "validation_rows": len(validation_dataset),
        "excluded_rows": len(dataset_frame) - len(training_frame),
        "model_id": metadata.model_id,
        "model_type": metadata.model_type,
        "output_dir": str(output_dir),
        "metadata_path": str(metadata_path),
        "model_path": str(model_path),
        "scaler_path": str(scaler_path),
        "feature_count": len(dataset.feature_columns),
        "feature_columns": list(dataset.feature_columns),
        "base_feature_names": list(tensorizer.feature_names),
        "context_length": tensorizer.context_length,
        "target_spec": target_spec.to_dict(),
        "transformer_config": asdict(transformer_config),
        "training_config": {
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "weight_decay": weight_decay,
            "seed": seed,
        },
        "metrics": metrics,
        "training_data": metadata.training_data.to_dict(),
        "schema_hash": metadata.schema_hash,
        "leakage_controls": {
            "input_is_train_only": input_is_train_only,
            "training_start": _optional_timestamp_text(training_start),
            "training_end": _optional_timestamp_text(training_end),
            "target_end_cutoff_enforced": (
                training_end is not None and _target_end_cutoff_column(dataset_frame) is not None
            ),
            "validation_is_tail_split": len(validation_dataset) > 0,
        },
    }
    write_json(payload, resolved_report_path)
    return payload


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Train and export a runtime-loadable transformer signal bundle.",
    )
    parser.add_argument("--input-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--training-start", default=None)
    parser.add_argument("--training-end", default=None)
    parser.add_argument(
        "--input-is-train-only",
        action="store_true",
        help="Treat the entire input as curated training-only data.",
    )
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument("--target-horizon", default="unknown")
    parser.add_argument("--validation-size", type=int, default=0)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--model-dim", type=int, default=64)
    parser.add_argument("--num-heads", type=int, default=4)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--feedforward-dim", type=int, default=128)
    parser.add_argument("--dropout", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--min-scale", type=float, default=1e-12)
    parser.add_argument("--report-output-path", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    payload = train_transformer_cli(
        input_path=args.input_path,
        output_dir=args.output_dir,
        model_id=args.model_id,
        training_start=args.training_start,
        training_end=args.training_end,
        input_is_train_only=args.input_is_train_only,
        dataset_id=args.dataset_id,
        target_horizon=args.target_horizon,
        validation_size=args.validation_size,
        validation_fraction=args.validation_fraction,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        model_dim=args.model_dim,
        num_heads=args.num_heads,
        num_layers=args.num_layers,
        feedforward_dim=args.feedforward_dim,
        dropout=args.dropout,
        seed=args.seed,
        min_scale=args.min_scale,
        report_output_path=args.report_output_path,
    )
    print(write_json(payload, None))


def _validate_training_arguments(
    *,
    model_id: str,
    validation_size: int,
    validation_fraction: float,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    seed: int,
    min_scale: float,
) -> None:
    if not model_id.strip():
        raise ValueError("model_id must be non-empty.")
    if validation_size < 0:
        raise ValueError("validation_size must be non-negative.")
    if not (0.0 <= validation_fraction < 1.0):
        raise ValueError("validation_fraction must be in the range [0.0, 1.0).")
    if epochs <= 0:
        raise ValueError("epochs must be greater than zero.")
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero.")
    if learning_rate <= 0:
        raise ValueError("learning_rate must be greater than zero.")
    if weight_decay < 0:
        raise ValueError("weight_decay must be non-negative.")
    if seed < 0:
        raise ValueError("seed must be non-negative.")
    if min_scale <= 0:
        raise ValueError("min_scale must be greater than zero.")


def _select_training_frame(
    frame: pd.DataFrame,
    *,
    training_start: str | datetime | pd.Timestamp | None,
    training_end: str | datetime | pd.Timestamp | None,
    input_is_train_only: bool,
) -> pd.DataFrame:
    if training_end is None and not input_is_train_only:
        raise ValueError(
            "Provide training_end or pass input_is_train_only for an already curated "
            "training-only dataset."
        )

    start_timestamp = _parse_optional_utc_timestamp(training_start, "training_start")
    end_timestamp = _parse_optional_utc_timestamp(training_end, "training_end")
    if (
        start_timestamp is not None
        and end_timestamp is not None
        and start_timestamp > end_timestamp
    ):
        raise ValueError("training_start must not be after training_end.")

    anchor_column = (
        "available_timestamp" if "available_timestamp" in frame.columns else "event_timestamp"
    )
    mask = pd.Series(True, index=frame.index)
    if start_timestamp is not None:
        mask &= pd.to_datetime(frame[anchor_column], utc=True) >= start_timestamp
    if end_timestamp is not None:
        mask &= pd.to_datetime(frame[anchor_column], utc=True) <= end_timestamp
        target_end_column = _target_end_cutoff_column(frame)
        if target_end_column is None:
            raise ValueError(
                "training_end requires target_end_timestamp or "
                "target_end_event_timestamp so labels cannot cross the cutoff."
            )
        mask &= pd.to_datetime(frame[target_end_column], utc=True) <= end_timestamp

    training_frame = frame.loc[mask].reset_index(drop=True)
    if training_frame.empty:
        raise ValueError("Training selection produced no rows.")
    return training_frame


def _split_train_validation_dataset(
    dataset: SupervisedDataset,
    *,
    validation_size: int,
    validation_fraction: float,
) -> tuple[SupervisedDataset, SupervisedDataset]:
    resolved_validation_size = validation_size
    if resolved_validation_size == 0 and validation_fraction > 0:
        resolved_validation_size = int(round(len(dataset) * validation_fraction))
    if resolved_validation_size >= len(dataset):
        raise ValueError("Validation split must leave at least one training row.")
    train_end = len(dataset) - resolved_validation_size
    return _slice_dataset(dataset, 0, train_end), _slice_dataset(dataset, train_end, len(dataset))


def _slice_dataset(dataset: SupervisedDataset, start: int, end: int) -> SupervisedDataset:
    features = dataset.features.iloc[start:end].reset_index(drop=True)
    targets = dataset.targets.iloc[start:end].reset_index(drop=True)
    metadata = dataset.metadata.iloc[start:end].reset_index(drop=True)
    return SupervisedDataset(features=features, targets=targets, metadata=metadata)


def _fit_feature_scaler(
    feature_frame: pd.DataFrame,
    *,
    feature_columns: Sequence[str],
    min_scale: float,
) -> _FeatureScaler:
    normalized_columns = tuple(str(column) for column in feature_columns)
    values = feature_frame.loc[:, list(normalized_columns)].to_numpy(dtype=float, copy=True)
    if not np.isfinite(values).all():
        raise ValueError("Training feature frame contains non-finite values.")
    means = values.mean(axis=0)
    scales = values.std(axis=0, ddof=0)
    scales = np.where(scales < min_scale, 1.0, scales)
    return _FeatureScaler(
        feature_columns=normalized_columns,
        feature_means=tuple(float(value) for value in means),
        feature_scales=tuple(float(value) for value in scales),
    )


def _scale_feature_frame(feature_frame: pd.DataFrame, scaler: _FeatureScaler) -> pd.DataFrame:
    values = feature_frame.loc[:, list(scaler.feature_columns)].to_numpy(dtype=float, copy=True)
    means = np.asarray(scaler.feature_means, dtype=float)
    scales = np.asarray(scaler.feature_scales, dtype=float)
    scaled = (values - means) / scales
    return pd.DataFrame(scaled, columns=scaler.feature_columns, index=feature_frame.index)


def _fit_transformer_model(
    *,
    train_dataset: SupervisedDataset,
    validation_dataset: SupervisedDataset,
    tensorizer: LaggedFeatureTensorizer,
    scaler: _FeatureScaler,
    config: TransformerSignalConfig,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    seed: int,
) -> tuple[TransformerSignalModel, float]:
    torch.manual_seed(seed)
    generator = torch.Generator()
    generator.manual_seed(seed)
    model = TransformerSignalModel(config)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=learning_rate,
        weight_decay=weight_decay,
    )
    loss_fn = nn.MSELoss()
    train_features = _scale_feature_frame(train_dataset.features, scaler)
    train_batch = tensorizer.build_batch(train_features, targets=train_dataset.targets)
    if train_batch.targets is None:
        raise ValueError("Training targets are required.")
    final_loss = 0.0
    for _ in range(epochs):
        model.train()
        permutation = torch.randperm(len(train_dataset), generator=generator)
        for batch_start in range(0, len(train_dataset), batch_size):
            indices = permutation[batch_start : batch_start + batch_size]
            optimizer.zero_grad(set_to_none=True)
            output = model(train_batch.inputs[indices])
            target = train_batch.targets[indices]
            loss = loss_fn(output.predictions, target)
            loss.backward()
            optimizer.step()
            final_loss = float(loss.detach().cpu().item())
    if len(validation_dataset) > 0:
        model.eval()
    return model, final_loss


def _training_metrics(
    *,
    model: TransformerSignalModel,
    tensorizer: LaggedFeatureTensorizer,
    scaler: _FeatureScaler,
    train_dataset: SupervisedDataset,
    validation_dataset: SupervisedDataset,
    final_training_loss: float,
) -> dict[str, float]:
    metrics = {
        "training_final_batch_loss": final_training_loss,
        **_prediction_metrics(
            prefix="training",
            model=model,
            tensorizer=tensorizer,
            scaler=scaler,
            dataset=train_dataset,
        ),
    }
    if len(validation_dataset) > 0:
        metrics.update(
            _prediction_metrics(
                prefix="validation",
                model=model,
                tensorizer=tensorizer,
                scaler=scaler,
                dataset=validation_dataset,
            )
        )
    return metrics


@torch.inference_mode()
def _prediction_metrics(
    *,
    prefix: str,
    model: TransformerSignalModel,
    tensorizer: LaggedFeatureTensorizer,
    scaler: _FeatureScaler,
    dataset: SupervisedDataset,
) -> dict[str, float]:
    scaled_features = _scale_feature_frame(dataset.features, scaler)
    batch = tensorizer.build_batch(scaled_features, targets=dataset.targets)
    if batch.targets is None:
        raise ValueError("Targets are required for metric calculation.")
    model.eval()
    predictions = model(batch.inputs).predictions
    errors = predictions - batch.targets
    prediction_array = predictions.detach().cpu().numpy().reshape(-1)
    target_array = batch.targets.detach().cpu().numpy().reshape(-1)
    directional_mask = target_array != 0.0
    directional_accuracy = 0.0
    if bool(directional_mask.any()):
        prediction_signs = np.sign(prediction_array[directional_mask])
        target_signs = np.sign(target_array[directional_mask])
        directional_accuracy = float((prediction_signs == target_signs).mean())
    return {
        f"{prefix}_mse": float(torch.mean(errors.square()).detach().cpu().item()),
        f"{prefix}_mae": float(torch.mean(errors.abs()).detach().cpu().item()),
        f"{prefix}_directional_accuracy": directional_accuracy,
        f"{prefix}_mean_prediction": float(prediction_array.mean()),
        f"{prefix}_mean_target": float(target_array.mean()),
    }


def _save_transformer_model_artifact(
    model: TransformerSignalModel,
    path: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "model_format_version": _MODEL_FORMAT_VERSION,
        "model_type": TRANSFORMER_SIGNAL_MODEL_TYPE,
        "config": asdict(model.config),
        "state_dict": model.state_dict(),
    }
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            temp_path = Path(handle.name)
            torch.save(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return path


def _scaler_payload(scaler: _FeatureScaler) -> dict[str, object]:
    return {
        "scaler_format_version": _SCALER_FORMAT_VERSION,
        "scaler_type": "standard_mean_scale",
        "feature_columns": list(scaler.feature_columns),
        "feature_means": list(scaler.feature_means),
        "feature_scales": list(scaler.feature_scales),
    }


def _transformer_config_metadata(
    config: TransformerSignalConfig,
) -> dict[str, JsonValue]:
    return {
        "input_size": config.input_size,
        "context_length": config.context_length,
        "model_dim": config.model_dim,
        "num_heads": config.num_heads,
        "num_layers": config.num_layers,
        "feedforward_dim": config.feedforward_dim,
        "dropout": config.dropout,
        "output_dim": config.output_dim,
        "activation": config.activation,
        "layer_norm_eps": config.layer_norm_eps,
    }


def _build_bundle_metadata(
    *,
    model_id: str,
    dataset: SupervisedDataset,
    input_path: Path,
    input_rows: int,
    input_is_train_only: bool,
    training_start: str | datetime | pd.Timestamp | None,
    training_end: str | datetime | pd.Timestamp | None,
    dataset_id: str,
    target_spec: ModelTargetSpec,
    model_artifact: ModelBundleArtifact,
    scaler_artifact: ModelBundleArtifact,
    metrics: dict[str, float],
    transformer_config: TransformerSignalConfig,
    seed: int,
) -> ModelBundleMetadata:
    training_window = _training_data_window(
        dataset=dataset,
        dataset_id=dataset_id,
        input_path=input_path,
        input_rows=input_rows,
        input_is_train_only=input_is_train_only,
        training_start=training_start,
        training_end=training_end,
    )
    return ModelBundleMetadata(
        model_id=model_id,
        model_type=TRANSFORMER_SIGNAL_MODEL_TYPE,
        trained_at=datetime.now(tz=UTC),
        feature_columns=dataset.feature_columns,
        target_spec=target_spec,
        scaler_artifact=scaler_artifact,
        model_artifact=model_artifact,
        schema_hash=compute_feature_contract_hash(
            dataset.feature_columns,
            target_spec=target_spec,
        ),
        metrics=metrics,
        training_data=training_window,
        metadata={
            "source_script": "scripts/train_transformer.py",
            "input_path": str(input_path),
            "input_rows": input_rows,
            "input_is_train_only": input_is_train_only,
            "training_start": _optional_timestamp_text(training_start),
            "training_end": _optional_timestamp_text(training_end),
            "transformer_config": _transformer_config_metadata(transformer_config),
            "seed": seed,
            "paper_mode_default": True,
        },
    )


def _training_data_window(
    *,
    dataset: SupervisedDataset,
    dataset_id: str,
    input_path: Path,
    input_rows: int,
    input_is_train_only: bool,
    training_start: str | datetime | pd.Timestamp | None,
    training_end: str | datetime | pd.Timestamp | None,
) -> TrainingDataWindow:
    metadata = dataset.metadata
    anchor_timestamps = pd.to_datetime(metadata["available_timestamp"], utc=True)
    target_end_column = _target_end_cutoff_column(metadata)
    end_timestamps = (
        pd.to_datetime(metadata[target_end_column], utc=True)
        if target_end_column is not None
        else anchor_timestamps
    )
    symbols = tuple(sorted(str(symbol) for symbol in metadata["symbol"].unique()))
    return TrainingDataWindow(
        dataset_id=dataset_id,
        symbols=symbols,
        window_start=anchor_timestamps.min().to_pydatetime().astimezone(UTC),
        window_end=end_timestamps.max().to_pydatetime().astimezone(UTC),
        row_count=len(dataset),
        metadata={
            "input_path": str(input_path),
            "input_rows": input_rows,
            "input_is_train_only": input_is_train_only,
            "training_start": _optional_timestamp_text(training_start),
            "training_end": _optional_timestamp_text(training_end),
            "anchor_timestamp_column": "available_timestamp",
            "target_end_timestamp_column": target_end_column,
        },
    )


def _target_end_cutoff_column(frame: pd.DataFrame) -> str | None:
    if TARGET_END_TIMESTAMP_COLUMN in frame.columns:
        return TARGET_END_TIMESTAMP_COLUMN
    if TARGET_END_EVENT_TIMESTAMP_COLUMN in frame.columns:
        return TARGET_END_EVENT_TIMESTAMP_COLUMN
    return None


def _parse_optional_utc_timestamp(
    value: str | datetime | pd.Timestamp | None,
    field_name: str,
) -> pd.Timestamp | None:
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError(f"{field_name} must be a valid timestamp.")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return timestamp.tz_convert("UTC")


def _optional_timestamp_text(value: str | datetime | pd.Timestamp | None) -> str | None:
    timestamp = _parse_optional_utc_timestamp(value, "timestamp")
    if timestamp is None:
        return None
    return str(timestamp.isoformat()).replace("+00:00", "Z")


if __name__ == "__main__":
    main()

"""Unit tests for runtime model bundle inference loading."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
import torch

from scalper_ai.data.datasets import SupervisedDataset
from scalper_ai.models import (
    BASELINE_FILTER_MODEL_TYPE,
    TRANSFORMER_SIGNAL_MODEL_TYPE,
    ModelBundleArtifact,
    ModelBundleMetadata,
    ModelTargetSpec,
    TrainingDataWindow,
    TransformerSignalConfig,
    TransformerSignalModel,
    compute_feature_contract_hash,
    fit_supervised_baseline_filter,
    hash_file_sha256,
    load_baseline_filter_inference_package,
    save_model_bundle_metadata,
    save_supervised_baseline_filter_model,
)
from scalper_ai.models.runtime import load_transformer_inference_package


def test_load_baseline_filter_inference_package_scores_runtime_features(
    tmp_path: Path,
) -> None:
    dataset = _dataset()
    bundle_dir = _write_bundle(tmp_path, dataset=dataset)

    package = load_baseline_filter_inference_package(bundle_dir)
    scores = package.score_frame(dataset.features)
    predictions = package.predict_frame(dataset.features)
    signal = package.predict_latest(
        dataset.features,
        symbol="EURUSD",
        timestamp=datetime(2026, 5, 3, 12, 4, tzinfo=UTC),
    )

    assert package.metadata.model_type == BASELINE_FILTER_MODEL_TYPE
    assert scores.iloc[-1] > scores.iloc[0]
    assert predictions.tolist() == [-1, -1, 1, 1]
    assert signal.model_id == "eurusd-filter"
    assert signal.prediction == 1
    assert signal.symbol == "EURUSD"
    assert signal.timestamp == datetime(2026, 5, 3, 12, 4, tzinfo=UTC)


def test_load_baseline_filter_inference_package_rejects_hash_mismatch(
    tmp_path: Path,
) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        dataset=_dataset(),
        model_sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="artifact hash mismatch"):
        load_baseline_filter_inference_package(bundle_dir)


def test_baseline_filter_inference_package_rejects_schema_mismatch(
    tmp_path: Path,
) -> None:
    dataset = _dataset()
    bundle_dir = _write_bundle(
        tmp_path,
        dataset=dataset,
        metadata_feature_columns=tuple(reversed(dataset.feature_columns)),
    )

    with pytest.raises(ValueError, match="Model feature columns must match"):
        load_baseline_filter_inference_package(bundle_dir)


def test_load_transformer_inference_package_scores_runtime_features(
    tmp_path: Path,
) -> None:
    dataset = _transformer_dataset()
    bundle_dir = _write_transformer_bundle(tmp_path, dataset=dataset)

    package = load_transformer_inference_package(bundle_dir)
    scores = package.score_frame(dataset.features)
    signal = package.predict_latest(
        dataset.features,
        symbol="EURUSD",
        timestamp=datetime(2026, 5, 3, 12, 3, tzinfo=UTC),
    )

    assert package.metadata.model_type == TRANSFORMER_SIGNAL_MODEL_TYPE
    assert len(scores) == len(dataset)
    assert scores.notna().all()
    assert signal.model_id == "eurusd-transformer"
    assert signal.symbol == "EURUSD"
    assert signal.timestamp == datetime(2026, 5, 3, 12, 3, tzinfo=UTC)


def test_load_transformer_inference_package_rejects_hash_mismatch(
    tmp_path: Path,
) -> None:
    bundle_dir = _write_transformer_bundle(
        tmp_path,
        dataset=_transformer_dataset(),
        model_sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="artifact hash mismatch"):
        load_transformer_inference_package(bundle_dir)


def _write_bundle(
    tmp_path: Path,
    *,
    dataset: SupervisedDataset,
    model_sha256: str | None = None,
    metadata_feature_columns: tuple[str, ...] | None = None,
) -> Path:
    model = fit_supervised_baseline_filter(dataset)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    model_path = bundle_dir / "model.json"
    scaler_path = bundle_dir / "scaler.json"
    save_supervised_baseline_filter_model(model, model_path)
    scaler_path.write_text(
        json.dumps(
            {
                "feature_columns": list(model.feature_columns),
                "feature_means": list(model.feature_means),
                "feature_scales": list(model.feature_scales),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    feature_columns = metadata_feature_columns or model.feature_columns
    target_spec = ModelTargetSpec(
        name="target",
        target_type="directional_regression_filter",
        horizon="1m",
        parameters={"target_threshold": 0.0},
    )
    metadata = ModelBundleMetadata(
        model_id="eurusd-filter",
        model_type=BASELINE_FILTER_MODEL_TYPE,
        trained_at=datetime(2026, 5, 3, 12, tzinfo=UTC),
        feature_columns=feature_columns,
        target_spec=target_spec,
        scaler_artifact=ModelBundleArtifact(
            name="scaler",
            path="scaler.json",
            sha256=hash_file_sha256(scaler_path),
        ),
        model_artifact=ModelBundleArtifact(
            name="model",
            path="model.json",
            sha256=model_sha256 or hash_file_sha256(model_path),
        ),
        schema_hash=compute_feature_contract_hash(
            feature_columns,
            target_spec=target_spec,
        ),
        metrics={"training_directional_accuracy": 1.0},
        training_data=TrainingDataWindow(
            dataset_id="unit",
            symbols=("EURUSD",),
            window_start=datetime(2026, 5, 3, 12, tzinfo=UTC),
            window_end=datetime(2026, 5, 3, 12, 3, tzinfo=UTC),
            row_count=len(dataset),
        ),
    )
    save_model_bundle_metadata(metadata, bundle_dir / "metadata.json")
    return bundle_dir


def _write_transformer_bundle(
    tmp_path: Path,
    *,
    dataset: SupervisedDataset,
    model_sha256: str | None = None,
) -> Path:
    bundle_dir = tmp_path / "transformer-bundle"
    bundle_dir.mkdir()
    model_path = bundle_dir / "model.pt"
    scaler_path = bundle_dir / "scaler.json"
    tensor_input_size = 1
    tensor_context_length = 2
    model = TransformerSignalModel(
        TransformerSignalConfig(
            input_size=tensor_input_size,
            context_length=tensor_context_length,
            model_dim=8,
            num_heads=2,
            num_layers=1,
            feedforward_dim=16,
            dropout=0.0,
            output_dim=1,
        )
    )
    torch.save(
        {
            "model_format_version": 1,
            "model_type": TRANSFORMER_SIGNAL_MODEL_TYPE,
            "config": {
                "input_size": tensor_input_size,
                "context_length": tensor_context_length,
                "model_dim": 8,
                "num_heads": 2,
                "num_layers": 1,
                "feedforward_dim": 16,
                "dropout": 0.0,
                "output_dim": 1,
                "activation": "gelu",
                "layer_norm_eps": 1e-5,
            },
            "state_dict": model.state_dict(),
        },
        model_path,
    )
    scaler_path.write_text(
        json.dumps(
            {
                "scaler_format_version": 1,
                "scaler_type": "standard_mean_scale",
                "feature_columns": list(dataset.feature_columns),
                "feature_means": [0.0, 0.0],
                "feature_scales": [1.0, 1.0],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    target_spec = ModelTargetSpec(
        name="target",
        target_type="regression_forecast",
        horizon="1m",
        parameters={"loss": "mse"},
    )
    metadata = ModelBundleMetadata(
        model_id="eurusd-transformer",
        model_type=TRANSFORMER_SIGNAL_MODEL_TYPE,
        trained_at=datetime(2026, 5, 3, 12, tzinfo=UTC),
        feature_columns=dataset.feature_columns,
        target_spec=target_spec,
        scaler_artifact=ModelBundleArtifact(
            name="scaler",
            path="scaler.json",
            sha256=hash_file_sha256(scaler_path),
        ),
        model_artifact=ModelBundleArtifact(
            name="model",
            path="model.pt",
            sha256=model_sha256 or hash_file_sha256(model_path),
        ),
        schema_hash=compute_feature_contract_hash(
            dataset.feature_columns,
            target_spec=target_spec,
        ),
        metrics={"training_mse": 1.0},
        training_data=TrainingDataWindow(
            dataset_id="unit",
            symbols=("EURUSD",),
            window_start=datetime(2026, 5, 3, 12, tzinfo=UTC),
            window_end=datetime(2026, 5, 3, 12, 3, tzinfo=UTC),
            row_count=len(dataset),
        ),
    )
    save_model_bundle_metadata(metadata, bundle_dir / "metadata.json")
    return bundle_dir


def _dataset() -> SupervisedDataset:
    features = pd.DataFrame(
        {
            "lag_000__signal": [-2.0, -1.0, 1.0, 2.0],
            "lag_001__signal": [-1.5, -1.0, 1.0, 1.5],
        }
    )
    start = datetime(2026, 5, 3, 12, tzinfo=UTC)
    metadata = pd.DataFrame(
        {
            "symbol": ["EURUSD"] * len(features),
            "event_timestamp": [
                start + timedelta(minutes=index) for index in range(len(features))
            ],
            "available_timestamp": [
                start + timedelta(minutes=index) for index in range(len(features))
            ],
            "feature_set": ["unit"] * len(features),
            "feature_version": ["1"] * len(features),
        }
    )
    return SupervisedDataset(
        features=features,
        targets=pd.Series([-0.003, -0.002, 0.002, 0.003], name="target"),
        metadata=metadata,
    )


def _transformer_dataset() -> SupervisedDataset:
    features = pd.DataFrame(
        {
            "lag_000__signal": [-2.0, -1.0, 1.0, 2.0],
            "lag_001__signal": [-1.5, -1.0, 1.0, 1.5],
        }
    )
    start = datetime(2026, 5, 3, 12, tzinfo=UTC)
    metadata = pd.DataFrame(
        {
            "symbol": ["EURUSD"] * len(features),
            "event_timestamp": [
                start + timedelta(minutes=index) for index in range(len(features))
            ],
            "available_timestamp": [
                start + timedelta(minutes=index) for index in range(len(features))
            ],
            "feature_set": ["unit"] * len(features),
            "feature_version": ["1"] * len(features),
        }
    )
    return SupervisedDataset(
        features=features,
        targets=pd.Series([-0.003, -0.002, 0.002, 0.003], name="target"),
        metadata=metadata,
    )

"""Unit tests for the transformer training/export CLI."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from scalper_ai.models import load_transformer_inference_package


def test_train_transformer_cli_writes_runtime_bundle(tmp_path: Path) -> None:
    script = _load_script_module("train_transformer")
    input_path = tmp_path / "supervised-dataset.csv"
    output_dir = tmp_path / "transformer-bundle"
    start = datetime(2026, 5, 3, 10, 0, tzinfo=UTC)
    training_end = start + timedelta(minutes=6)
    _supervised_dataset_frame(row_count=10, start=start).to_csv(input_path, index=False)

    payload = script.train_transformer_cli(
        input_path=input_path,
        output_dir=output_dir,
        model_id="eurusd-transformer-20260503",
        training_end=training_end,
        dataset_id="unit-dataset",
        target_horizon="1m",
        validation_size=2,
        epochs=2,
        batch_size=2,
        learning_rate=0.005,
        model_dim=8,
        num_heads=2,
        num_layers=1,
        feedforward_dim=16,
        dropout=0.0,
        seed=11,
    )

    report = json.loads((output_dir / "training-report.json").read_text(encoding="utf-8"))
    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    package = load_transformer_inference_package(output_dir)
    training_frame = pd.read_csv(input_path).head(6)
    scores = package.score_frame(training_frame.loc[:, package.metadata.feature_columns])

    assert (output_dir / "model.pt").exists()
    assert (output_dir / "scaler.json").exists()
    assert payload["training_rows"] == 6
    assert payload["fit_rows"] == 4
    assert payload["validation_rows"] == 2
    assert payload["excluded_rows"] == 4
    assert payload["context_length"] == 2
    assert report["model_id"] == "eurusd-transformer-20260503"
    assert metadata["model_type"] == "transformer_signal"
    assert metadata["training_data"]["dataset_id"] == "unit-dataset"
    assert metadata["training_data"]["window_end"] == training_end.isoformat().replace(
        "+00:00",
        "Z",
    )
    assert metadata["model_artifact"]["sha256"]
    assert metadata["scaler_artifact"]["sha256"]
    assert report["leakage_controls"]["target_end_cutoff_enforced"] is True
    assert report["leakage_controls"]["validation_is_tail_split"] is True
    assert len(scores) == 6
    assert scores.notna().all()


def test_train_transformer_cli_requires_training_cutoff_or_train_only(
    tmp_path: Path,
) -> None:
    script = _load_script_module("train_transformer")
    input_path = tmp_path / "supervised-dataset.csv"
    _supervised_dataset_frame(row_count=6).to_csv(input_path, index=False)

    with pytest.raises(ValueError, match="Provide training_end"):
        script.train_transformer_cli(
            input_path=input_path,
            output_dir=tmp_path / "bundle",
            model_id="eurusd-transformer",
            epochs=1,
        )


def test_train_transformer_cli_allows_curated_train_only_input(
    tmp_path: Path,
) -> None:
    script = _load_script_module("train_transformer")
    input_path = tmp_path / "train-only.csv"
    _supervised_dataset_frame(row_count=6).drop(
        columns=["target_end_timestamp", "target_end_event_timestamp"]
    ).to_csv(input_path, index=False)

    payload = script.train_transformer_cli(
        input_path=input_path,
        output_dir=tmp_path / "bundle",
        model_id="eurusd-transformer",
        input_is_train_only=True,
        validation_size=1,
        epochs=1,
        batch_size=2,
        model_dim=8,
        num_heads=2,
        num_layers=1,
        feedforward_dim=16,
    )

    assert payload["training_rows"] == 6
    assert payload["leakage_controls"]["input_is_train_only"] is True
    assert load_transformer_inference_package(tmp_path / "bundle").metadata.model_id == (
        "eurusd-transformer"
    )


def _load_script_module(name: str) -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _supervised_dataset_frame(
    *,
    row_count: int,
    start: datetime | None = None,
) -> pd.DataFrame:
    resolved_start = start or datetime(2026, 5, 3, 10, 0, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for index in range(row_count):
        timestamp = resolved_start + timedelta(minutes=index)
        target_end = timestamp + timedelta(minutes=1)
        signal = 1.0 if index % 4 < 2 else -1.0
        previous_signal = 1.0 if (index - 1) % 4 < 2 else -1.0
        rows.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "available_timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "feature_set": "unit",
                "feature_version": "1",
                "target_end_timestamp": target_end.isoformat().replace("+00:00", "Z"),
                "target_end_event_timestamp": target_end.isoformat().replace(
                    "+00:00",
                    "Z",
                ),
                "lag_000__signal": signal,
                "lag_000__mid_return": signal * 0.0002,
                "lag_001__signal": previous_signal,
                "lag_001__mid_return": previous_signal * 0.0002,
                "target": signal * 0.002,
            }
        )
    return pd.DataFrame.from_records(rows)

"""Unit tests for the supervised filter training/export CLI."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from scalper_ai.models import load_baseline_filter_inference_package


def test_train_supervised_filter_cli_writes_runtime_bundle(tmp_path: Path) -> None:
    script = _load_script_module("train_supervised_filter")
    input_path = tmp_path / "supervised-dataset.csv"
    output_dir = tmp_path / "bundle"
    start = datetime(2026, 5, 3, 10, 0, tzinfo=UTC)
    training_end = start + timedelta(minutes=5)
    _supervised_dataset_frame(row_count=10, start=start).to_csv(input_path, index=False)

    payload = script.train_supervised_filter_cli(
        input_path=input_path,
        output_dir=output_dir,
        model_id="eurusd-filter-20260503",
        training_end=training_end,
        dataset_id="unit-dataset",
        target_horizon="1m",
        target_threshold=0.0001,
        score_threshold=0.0,
        top_features_limit=2,
    )

    report = json.loads((output_dir / "training-report.json").read_text(encoding="utf-8"))
    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    package = load_baseline_filter_inference_package(output_dir)
    training_frame = pd.read_csv(input_path).head(5)

    assert (output_dir / "model.json").exists()
    assert (output_dir / "scaler.json").exists()
    assert (output_dir / "feature_importance.csv").exists()
    assert payload["training_rows"] == 5
    assert payload["excluded_rows"] == 5
    assert report["model_id"] == "eurusd-filter-20260503"
    assert metadata["model_type"] == "supervised_baseline_filter"
    assert metadata["training_data"]["dataset_id"] == "unit-dataset"
    assert metadata["training_data"]["window_end"] == training_end.isoformat().replace(
        "+00:00",
        "Z",
    )
    assert metadata["model_artifact"]["sha256"]
    assert metadata["scaler_artifact"]["sha256"]
    assert report["leakage_controls"]["target_end_cutoff_enforced"] is True
    assert len(report["top_features"]) == 2
    predictions = package.predict_frame(training_frame.loc[:, package.metadata.feature_columns])
    assert set(predictions.tolist()).issubset({-1, 0, 1})


def test_train_supervised_filter_cli_requires_training_cutoff_or_train_only(
    tmp_path: Path,
) -> None:
    script = _load_script_module("train_supervised_filter")
    input_path = tmp_path / "supervised-dataset.csv"
    _supervised_dataset_frame(row_count=6).to_csv(input_path, index=False)

    with pytest.raises(ValueError, match="Provide training_end"):
        script.train_supervised_filter_cli(
            input_path=input_path,
            output_dir=tmp_path / "bundle",
            model_id="eurusd-filter",
        )


def test_train_supervised_filter_cli_allows_curated_train_only_input(
    tmp_path: Path,
) -> None:
    script = _load_script_module("train_supervised_filter")
    input_path = tmp_path / "train-only.csv"
    _supervised_dataset_frame(row_count=6).drop(
        columns=["target_end_timestamp", "target_end_event_timestamp"]
    ).to_csv(input_path, index=False)

    payload = script.train_supervised_filter_cli(
        input_path=input_path,
        output_dir=tmp_path / "bundle",
        model_id="eurusd-filter",
        input_is_train_only=True,
    )

    assert payload["training_rows"] == 6
    assert payload["leakage_controls"]["input_is_train_only"] is True
    assert load_baseline_filter_inference_package(tmp_path / "bundle").metadata.model_id == (
        "eurusd-filter"
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
    signals = [1.0 if index % 4 < 2 else -1.0 for index in range(row_count)]
    for index, signal in enumerate(signals):
        timestamp = resolved_start + timedelta(minutes=index)
        target_end = timestamp + timedelta(minutes=1)
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
                "lag_000__mid_price": 1.1000 + index * 0.0001,
                "lag_000__signal": signal,
                "lag_001__signal": signals[index - 1] if index > 0 else signal,
                "lag_000__spread_bps": 0.5,
                "target": signal * 0.002,
            }
        )
    return pd.DataFrame.from_records(rows)

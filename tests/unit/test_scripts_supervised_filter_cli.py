"""Unit tests for the supervised filter CLI entrypoint."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pandas as pd


def test_run_supervised_filter_cli_writes_report_and_fold_metrics(
    tmp_path: Path,
) -> None:
    script = _load_script_module("run_supervised_filter")
    input_path = tmp_path / "supervised-dataset.csv"
    output_path = tmp_path / "supervised-filter.json"
    fold_metrics_path = tmp_path / "supervised-filter-folds.csv"
    _supervised_dataset_frame(row_count=24).to_csv(input_path, index=False)

    payload = script.run_supervised_filter_cli(
        input_path=input_path,
        output_path=output_path,
        fold_metrics_output_path=fold_metrics_path,
        train_size=6,
        validation_size=2,
        test_size=3,
        embargo_size=1,
        step_size=3,
        target_threshold=0.0001,
        score_threshold=0.0,
        spread_bps=0.4,
        slippage_bps=0.1,
        commission_bps=0.05,
        top_features_limit=3,
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    fold_metrics = pd.read_csv(fold_metrics_path)
    assert output_path.exists()
    assert fold_metrics_path.exists()
    assert payload["strategy"] == "supervised_baseline_filter"
    assert report["strategy"] == "supervised_baseline_filter"
    assert report["backtest_config"]["spread_bps"] == 0.4
    assert report["backtest_config"]["slippage_bps"] == 0.1
    assert report["backtest_config"]["commission_bps"] == 0.05
    assert report["cost_model"]["applied_to_directional_filter_metrics"] is False

    summary = report["summary"]
    assert summary["fold_count"] == 4
    assert summary["total_test_size"] == 12
    assert summary["total_evaluated_count"] == 12
    assert summary["mean_accuracy"] >= 0.75
    assert len(report["fold_metrics"]) == summary["fold_count"]
    assert len(fold_metrics) == summary["fold_count"]
    assert fold_metrics.loc[0, "split_index"] == 0

    filter_report = report["filter_report"]
    assert filter_report["name"] == "supervised_baseline_filter"
    assert filter_report["top_features"]
    assert filter_report["feature_importance"]
    assert filter_report["last_fold_model"]["top_features"]
    assert filter_report["top_features"][0]["feature"].endswith("__signal")

    first_fold_metrics = report["fold_metrics"][0]
    for column in (
        "train_end_timestamp",
        "validation_end_timestamp",
        "test_end_timestamp",
    ):
        assert first_fold_metrics[column].endswith("Z")
        _assert_utc_timestamp(first_fold_metrics[column])


def _load_script_module(name: str) -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_utc_timestamp(value: str) -> None:
    timestamp = pd.Timestamp(value)
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset() == timedelta(0)


def _supervised_dataset_frame(*, row_count: int) -> pd.DataFrame:
    start = datetime(2026, 5, 3, 10, 0, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    signals = [1.0 if index % 4 < 2 else -1.0 for index in range(row_count)]
    for index, signal in enumerate(signals):
        timestamp = start + timedelta(minutes=index)
        target_end = timestamp + timedelta(minutes=1)
        rows.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "available_timestamp": (
                    timestamp + timedelta(milliseconds=50)
                ).isoformat().replace("+00:00", "Z"),
                "feature_set": "unit",
                "feature_version": "1",
                "target_end_timestamp": (
                    target_end + timedelta(milliseconds=50)
                ).isoformat().replace("+00:00", "Z"),
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

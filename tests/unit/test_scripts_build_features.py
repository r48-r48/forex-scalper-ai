"""Unit tests for the offline feature-building CLI."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pandas as pd


def test_build_features_cli_writes_feature_frame_and_summary(tmp_path: Path) -> None:
    script = _load_script_module("build_features")
    input_path = tmp_path / "ticks.csv"
    output_path = tmp_path / "features.parquet"
    summary_path = tmp_path / "features-summary.json"
    _tick_frame().to_csv(input_path, index=False)

    payload = script.build_features_cli(
        input_path=input_path,
        output_path=output_path,
        summary_output_path=summary_path,
        venue="TEST",
        volatility_window=2,
        quote_intensity_window_seconds=10.0,
        ofi_window=2,
        toxicity_window=2,
        mlofi_depth=2,
    )

    feature_frame = pd.read_parquet(output_path)
    summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))

    assert output_path.exists()
    assert summary_path.exists()
    assert payload["input_rows"] == 3
    assert payload["feature_rows"] == 3
    assert summary_payload["feature_rows"] == 3
    assert payload["columns"] == list(feature_frame.columns)
    assert "spread" in payload["feature_columns"]
    assert "spread_bps" in payload["feature_columns"]
    assert "mid_return" in payload["feature_columns"]
    assert "ofi" in payload["feature_columns"]
    assert "toxicity_vpin" in payload["feature_columns"]
    assert "mlofi_l1" in payload["feature_columns"]
    assert "mlofi_l2" in payload["feature_columns"]
    assert isinstance(feature_frame["event_timestamp"].dtype, pd.DatetimeTZDtype)
    assert isinstance(feature_frame["available_timestamp"].dtype, pd.DatetimeTZDtype)
    assert str(feature_frame["event_timestamp"].dt.tz) == "UTC"
    assert summary_payload["preview"][0]["event_timestamp"].endswith("Z")
    assert summary_payload["preview"][0]["available_timestamp"].endswith("Z")


def _load_script_module(name: str) -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tick_frame() -> pd.DataFrame:
    start = datetime(2026, 5, 3, 10, 0, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for index, (bid, ask) in enumerate(
        (
            (1.1000, 1.1002),
            (1.1001, 1.1003),
            (1.1000, 1.1002),
        )
    ):
        event_timestamp = start + timedelta(seconds=index)
        rows.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": event_timestamp.isoformat().replace("+00:00", "Z"),
                "received_timestamp": (
                    event_timestamp + timedelta(milliseconds=50)
                ).isoformat().replace("+00:00", "Z"),
                "bid": bid,
                "ask": ask,
                "bid_size": 2.0 + index,
                "ask_size": 3.0 - index * 0.25,
                "last_price": (bid + ask) / 2.0,
                "last_size": 1.0 + index * 0.5,
            }
        )
    return pd.DataFrame.from_records(rows)

"""Unit tests for the historical data bootstrap CLI."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest


def test_bootstrap_history_cli_writes_normalized_frame_quality_and_summary(
    tmp_path: Path,
) -> None:
    script = _load_script_module("bootstrap_history")
    input_path = tmp_path / "broker-export.csv"
    output_path = tmp_path / "history.parquet"
    summary_path = tmp_path / "history-summary.json"
    quality_path = tmp_path / "history-quality.json"
    _tick_frame(include_received_timestamp=False).to_csv(input_path, index=False)

    payload = script.bootstrap_history_cli(
        input_path=input_path,
        output_path=output_path,
        dataset_id="eurusd-m1-demo-20260504",
        summary_output_path=summary_path,
        quality_report_path=quality_path,
        symbol="EURUSD",
        venue="MT5",
        timeframe="M1",
        max_event_gap_seconds=60.0,
    )

    history_frame = pd.read_parquet(output_path)
    summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    quality_payload = json.loads(quality_path.read_text(encoding="utf-8"))

    assert output_path.exists()
    assert payload["output_written"] is True
    assert payload["record_count"] == 3
    assert payload["quality"]["passed"] is True
    assert summary_payload["dataset_id"] == "eurusd-m1-demo-20260504"
    assert quality_payload["passed"] is True
    assert history_frame["dataset_id"].tolist() == ["eurusd-m1-demo-20260504"] * 3
    assert history_frame["symbol"].tolist() == ["EURUSD"] * 3
    assert history_frame["venue"].tolist() == ["MT5"] * 3
    assert isinstance(history_frame["event_timestamp"].dtype, pd.DatetimeTZDtype)
    assert isinstance(history_frame["received_timestamp"].dtype, pd.DatetimeTZDtype)
    assert str(history_frame["event_timestamp"].dt.tz) == "UTC"
    assert history_frame["received_timestamp"].equals(history_frame["event_timestamp"])
    assert history_frame["mid_price"].tolist() == pytest.approx([1.1001, 1.1002, 1.1001])
    assert payload["assumptions"]["received_timestamp_source"] == "filled_from_event_timestamp"
    assert payload["assumptions"]["bid_ask_source"] == "columns:bid,ask"
    assert payload["safety"]["order_send_called"] is False
    assert summary_payload["preview"][0]["event_timestamp"].endswith("Z")
    assert summary_payload["preview"][0]["received_timestamp"].endswith("Z")


def test_bootstrap_history_cli_maps_custom_columns_and_explicit_synthetic_spread(
    tmp_path: Path,
) -> None:
    script = _load_script_module("bootstrap_history")
    input_path = tmp_path / "mid-history.csv"
    output_path = tmp_path / "history.csv"
    summary_path = tmp_path / "summary.json"
    start = datetime(2026, 5, 4, 7, 0, tzinfo=UTC)
    frame = pd.DataFrame(
        {
            "time": [
                (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
                for index in range(2)
            ],
            "broker_symbol": ["EURUSD", "EURUSD"],
            "broker_venue": ["MT5", "MT5"],
            "mid": [1.2000, 1.2003],
        }
    )
    frame.to_csv(input_path, index=False)

    payload = script.bootstrap_history_cli(
        input_path=input_path,
        output_path=output_path,
        dataset_id="eurusd-mid-demo",
        summary_output_path=summary_path,
        event_timestamp_column="time",
        symbol_column="broker_symbol",
        venue_column="broker_venue",
        mid_price_column="mid",
        synthetic_spread_bps=2.0,
    )

    history_frame = pd.read_csv(output_path)
    expected_half_spread = frame["mid"] * (2.0 / 10_000.0) / 2.0

    assert payload["output_written"] is True
    assert payload["assumptions"]["bid_ask_source"] == "synthetic_mid_spread:mid"
    assert payload["assumptions"]["synthetic_spread_bps"] == 2.0
    assert history_frame["bid"].tolist() == pytest.approx(
        (frame["mid"] - expected_half_spread).tolist()
    )
    assert history_frame["ask"].tolist() == pytest.approx(
        (frame["mid"] + expected_half_spread).tolist()
    )
    assert json.loads(summary_path.read_text(encoding="utf-8"))["quality"]["passed"] is True


def test_bootstrap_history_cli_writes_quality_report_and_blocks_bad_output(
    tmp_path: Path,
) -> None:
    script = _load_script_module("bootstrap_history")
    input_path = tmp_path / "bad-history.csv"
    output_path = tmp_path / "history.parquet"
    summary_path = tmp_path / "summary.json"
    quality_path = tmp_path / "quality.json"
    frame = _tick_frame(include_received_timestamp=True)
    frame.loc[1, "ask"] = 1.0998
    frame.to_csv(input_path, index=False)

    with pytest.raises(ValueError, match="quality validation failed"):
        script.bootstrap_history_cli(
            input_path=input_path,
            output_path=output_path,
            dataset_id="bad-eurusd",
            summary_output_path=summary_path,
            quality_report_path=quality_path,
            venue="MT5",
        )

    summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    quality_payload = json.loads(quality_path.read_text(encoding="utf-8"))

    assert not output_path.exists()
    assert summary_payload["output_written"] is False
    assert summary_payload["quality"]["passed"] is False
    assert quality_payload["error_count"] == 1
    assert quality_payload["issues"][0]["code"] == "crossed_quote"


def _load_script_module(name: str) -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tick_frame(*, include_received_timestamp: bool) -> pd.DataFrame:
    start = datetime(2026, 5, 4, 6, 0, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for index, (bid, ask) in enumerate(
        (
            (1.1000, 1.1002),
            (1.1001, 1.1003),
            (1.1000, 1.1002),
        )
    ):
        event_timestamp = start + timedelta(minutes=index)
        row: dict[str, object] = {
            "symbol": "EURUSD",
            "event_timestamp": event_timestamp.isoformat().replace("+00:00", "Z"),
            "bid": bid,
            "ask": ask,
            "bid_size": 2.0 + index,
            "ask_size": 2.5 + index,
        }
        if include_received_timestamp:
            row["received_timestamp"] = (
                event_timestamp + timedelta(milliseconds=25)
            ).isoformat().replace("+00:00", "Z")
        rows.append(row)
    return pd.DataFrame.from_records(rows)

"""Unit tests for the Dukascopy historical tick downloader."""

from __future__ import annotations

import importlib.util
import json
import lzma
import struct
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest


def test_parse_bi5_ticks_decodes_utc_bid_ask_records() -> None:
    script = _load_script_module("download_dukascopy_ticks")
    payload = _compressed_bi5_payload(
        (
            (0, 110_020, 110_000, 2.5, 2.0),
            (1_500, 110_030, 110_010, 3.5, 3.0),
        )
    )

    records = script._parse_bi5_ticks(
        payload,
        symbol="EURUSD",
        trading_date=date(2026, 5, 4),
        hour=7,
        price_scale=100_000.0,
    )

    assert records == [
        {
            "symbol": "EURUSD",
            "event_timestamp": "2026-05-04T07:00:00Z",
            "bid": 1.1,
            "ask": 1.1002,
            "bid_size": 2.0,
            "ask_size": 2.5,
            "sequence": 70_000_000,
            "source": "dukascopy-datafeed",
        },
        {
            "symbol": "EURUSD",
            "event_timestamp": "2026-05-04T07:00:01.500000Z",
            "bid": 1.1001,
            "ask": 1.1003,
            "bid_size": 3.0,
            "ask_size": 3.5,
            "sequence": 70_000_001,
            "source": "dukascopy-datafeed",
        },
    ]


def test_download_dukascopy_ticks_cli_materializes_tick_and_bars(
    tmp_path: Path,
) -> None:
    script = _load_script_module("download_dukascopy_ticks")
    payload = _compressed_bi5_payload(
        (
            (0, 110_020, 110_000, 2.5, 2.0),
            (60_000, 110_030, 110_010, 3.5, 3.0),
        )
    )

    def fake_download_bytes(url: str, *, timeout_seconds: float) -> bytes | None:
        assert timeout_seconds == 5.0
        if url.endswith("/00h_ticks.bi5"):
            return payload
        return None

    script._download_bytes = fake_download_bytes

    result = script.download_dukascopy_ticks_cli(
        symbol="eurusd",
        trading_date=date(2026, 5, 4),
        vendor_output_dir=tmp_path / "vendor",
        parsed_output_dir=tmp_path / "parsed",
        bootstrap_output_dir=tmp_path / "history",
        bars_output_dir=tmp_path / "bars",
        summary_output_path=tmp_path / "summary.json",
        bootstrap_summary_output_path=tmp_path / "bootstrap-summary.json",
        quality_report_path=tmp_path / "quality.json",
        timeout_seconds=5.0,
        timeframes=("TICK", "M1", "H1", "D1"),
        compression="none",
        hour_workers=2,
    )

    tick_path = tmp_path / "history" / "EURUSD" / "TICK" / "date=2026-05-04.parquet"
    m1_path = tmp_path / "bars" / "EURUSD" / "M1" / "date=2026-05-04.parquet"
    h1_path = tmp_path / "bars" / "EURUSD" / "H1" / "date=2026-05-04.parquet"
    d1_path = tmp_path / "bars" / "EURUSD" / "D1" / "date=2026-05-04.parquet"
    parsed_path = tmp_path / "parsed" / "EURUSD" / "EURUSD-2026-05-04-ticks.csv"
    summary_payload = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    assert tick_path.exists()
    assert m1_path.exists()
    assert h1_path.exists()
    assert d1_path.exists()
    assert parsed_path.exists()
    assert result["completed_date_count"] == 1
    assert result["row_count"] == 2
    assert summary_payload["downloaded_hour_count"] == 1
    assert summary_payload["downloaded_hours"][0]["cached"] is False
    assert summary_payload["quality"]["passed"] is True
    assert summary_payload["safety"]["order_send_called"] is False
    assert pd.read_parquet(tick_path)["symbol"].tolist() == ["EURUSD", "EURUSD"]
    assert pd.read_parquet(m1_path)["tick_count"].tolist() == [1, 1]
    assert pd.read_parquet(h1_path)["tick_count"].tolist() == [2]
    assert pd.read_parquet(d1_path)["tick_count"].tolist() == [2]


def test_sorted_parsed_tick_frame_uses_real_utc_timestamp_order() -> None:
    script = _load_script_module("download_dukascopy_ticks")
    sorted_frame = script._sorted_parsed_tick_frame(
        [
            {
                "symbol": "EURUSD",
                "event_timestamp": "2024-01-02T02:41:12.253000Z",
                "bid": 1.1,
                "ask": 1.1001,
                "sequence": 1,
                "source": "unit",
            },
            {
                "symbol": "EURUSD",
                "event_timestamp": "2024-01-02T02:41:12Z",
                "bid": 1.1,
                "ask": 1.1001,
                "sequence": 2,
                "source": "unit",
            },
        ]
    )

    assert sorted_frame["event_timestamp"].tolist() == [
        "2024-01-02T02:41:12Z",
        "2024-01-02T02:41:12.253000Z",
    ]


def test_multi_day_download_records_no_data_days_without_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module("download_dukascopy_ticks")

    def fake_download_bytes(url: str, *, timeout_seconds: float) -> bytes | None:
        return None

    script._download_bytes = fake_download_bytes
    monkeypatch.chdir(tmp_path)

    result = script.download_dukascopy_ticks_cli(
        symbol="EURUSD",
        start_date=date(2026, 5, 2),
        end_date=date(2026, 5, 3),
        vendor_output_dir=tmp_path / "vendor",
        bootstrap_output_dir=tmp_path / "history",
        summary_output_path=tmp_path / "range-summary.json",
        timeframes=("TICK", "M1"),
        day_workers=2,
        hour_workers=2,
    )
    compact = script._console_payload(result)

    assert result["completed_date_count"] == 0
    assert result["skipped_date_count"] == 2
    assert {daily["reason"] for daily in result["daily"]} == {"no_data_available"}
    assert (tmp_path / "range-summary.json").exists()
    assert compact["daily"]["reason_counts"] == {"no_data_available": 2}


def _compressed_bi5_payload(records: tuple[tuple[int, int, int, float, float], ...]) -> bytes:
    raw_payload = b"".join(struct.pack(">IIIff", *record) for record in records)
    return lzma.compress(raw_payload)


def _load_script_module(name: str) -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

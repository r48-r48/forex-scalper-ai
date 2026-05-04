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


def test_download_archives_only_caches_raw_without_parsing(
    tmp_path: Path,
) -> None:
    script = _load_script_module("download_dukascopy_ticks")
    payload = _compressed_bi5_payload(((0, 110_020, 110_000, 2.5, 2.0),))

    def fake_download_bytes(url: str, *, timeout_seconds: float) -> bytes | None:
        assert timeout_seconds == 5.0
        if url.endswith("/00h_ticks.bi5"):
            return payload
        return None

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("raw-only mode must not parse or bootstrap data")

    script._download_bytes = fake_download_bytes
    script._parse_bi5_ticks = fail_if_called
    script.bootstrap_history_cli = fail_if_called
    script._write_timeframe_bars = fail_if_called

    result = script.download_dukascopy_ticks_cli(
        symbol="eurusd",
        trading_date=date(2026, 5, 4),
        vendor_output_dir=tmp_path / "vendor",
        parsed_output_dir=tmp_path / "parsed",
        bootstrap_output_dir=tmp_path / "history",
        bars_output_dir=tmp_path / "bars",
        summary_output_path=tmp_path / "raw-summary.json",
        timeout_seconds=5.0,
        download_archives_only=True,
        hour_workers=2,
    )

    archive_path = tmp_path / "vendor" / "EURUSD" / "2026-05-04" / "00h_ticks.bi5"
    summary_payload = json.loads(
        (tmp_path / "raw-summary.json").read_text(encoding="utf-8")
    )

    assert archive_path.read_bytes() == payload
    assert not (tmp_path / "parsed").exists()
    assert not (tmp_path / "history").exists()
    assert not (tmp_path / "bars").exists()
    assert result["processing_mode"] == "download_archives_only"
    assert result["completed_date_count"] == 1
    assert result["byte_count"] == len(payload)
    assert summary_payload["processing_mode"] == "download_archives_only"
    assert summary_payload["download_archives_only"] is True
    assert summary_payload["downloaded_hour_count"] == 1
    assert summary_payload["downloaded_hours"][0]["cached"] is False
    assert summary_payload["safety"]["order_send_called"] is False


def test_offline_cache_only_processes_cached_archives_without_http(
    tmp_path: Path,
) -> None:
    script = _load_script_module("download_dukascopy_ticks")
    payload = _compressed_bi5_payload(
        (
            (0, 110_020, 110_000, 2.5, 2.0),
            (60_000, 110_030, 110_010, 3.5, 3.0),
        )
    )
    archive_path = tmp_path / "vendor" / "EURUSD" / "2026-05-04" / "00h_ticks.bi5"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(payload)

    def fail_download_bytes(url: str, *, timeout_seconds: float) -> bytes | None:
        raise AssertionError("offline cache processing must not call Dukascopy")

    script._download_bytes = fail_download_bytes

    result = script.download_dukascopy_ticks_cli(
        symbol="EURUSD",
        trading_date=date(2026, 5, 4),
        vendor_output_dir=tmp_path / "vendor",
        parsed_output_dir=tmp_path / "parsed",
        bootstrap_output_dir=tmp_path / "history",
        bars_output_dir=tmp_path / "bars",
        summary_output_path=tmp_path / "summary.json",
        timeframes=("TICK", "M1"),
        compression="none",
        offline_cache_only=True,
        hour_workers=2,
    )

    tick_path = tmp_path / "history" / "EURUSD" / "TICK" / "date=2026-05-04.parquet"
    m1_path = tmp_path / "bars" / "EURUSD" / "M1" / "date=2026-05-04.parquet"
    summary_payload = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    assert tick_path.exists()
    assert m1_path.exists()
    assert result["completed_date_count"] == 1
    assert result["row_count"] == 2
    assert summary_payload["downloaded_hour_count"] == 1
    assert summary_payload["downloaded_hours"][0]["cached"] is True
    assert summary_payload["safety"]["order_send_called"] is False


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


def test_archives_only_multi_day_records_no_data_days_without_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module("download_dukascopy_ticks")

    def fake_download_bytes(url: str, *, timeout_seconds: float) -> bytes | None:
        return None

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("raw-only no-data mode must not bootstrap data")

    script._download_bytes = fake_download_bytes
    script.bootstrap_history_cli = fail_if_called
    monkeypatch.chdir(tmp_path)

    result = script.download_dukascopy_ticks_cli(
        symbol="EURUSD",
        start_date=date(2026, 5, 2),
        end_date=date(2026, 5, 3),
        vendor_output_dir=tmp_path / "vendor",
        bootstrap_output_dir=tmp_path / "history",
        summary_output_path=tmp_path / "range-summary.json",
        download_archives_only=True,
        day_workers=2,
        hour_workers=2,
    )

    assert result["processing_mode"] == "download_archives_only"
    assert result["completed_date_count"] == 0
    assert result["skipped_date_count"] == 2
    assert {daily["reason"] for daily in result["daily"]} == {"no_data_available"}
    assert (tmp_path / "range-summary.json").exists()
    assert (
        tmp_path
        / "data"
        / "artifacts"
        / "dukascopy"
        / "EURUSD"
        / "2026-05-02"
        / "raw-archive-summary.json"
    ).exists()


def test_defer_incomplete_repair_skips_failed_hour_day(
    tmp_path: Path,
) -> None:
    script = _load_script_module("download_dukascopy_ticks")
    summary_path = tmp_path / "summary.json"
    tick_path = tmp_path / "history" / "EURUSD" / "TICK" / "date=2026-05-04.parquet"
    bar_path = tmp_path / "bars" / "EURUSD" / "M1" / "date=2026-05-04.parquet"
    tick_path.parent.mkdir(parents=True)
    bar_path.parent.mkdir(parents=True)
    tick_path.write_bytes(b"existing")
    bar_path.write_bytes(b"existing")
    summary_path.write_text(json.dumps({"failed_hour_count": 1}), encoding="utf-8")

    def fake_download_one_day(**kwargs: object) -> dict[str, object]:
        raise AssertionError("incomplete days should be left for the repair pass")

    script._download_one_day = fake_download_one_day

    result = script.download_dukascopy_ticks_cli(
        symbol="EURUSD",
        trading_date=date(2026, 5, 4),
        vendor_output_dir=tmp_path / "vendor",
        bootstrap_output_dir=tmp_path / "history",
        bars_output_dir=tmp_path / "bars",
        summary_output_path=summary_path,
        timeframes=("TICK", "M1"),
        defer_incomplete_repair=True,
    )

    daily = result["daily"][0]
    assert result["skipped_date_count"] == 1
    assert daily["reason"] == "deferred_incomplete_day_repair"


def test_defer_incomplete_repair_skips_failed_day_summary(
    tmp_path: Path,
) -> None:
    script = _load_script_module("download_dukascopy_ticks")
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps({"skipped": True, "reason": "day_download_failed"}),
        encoding="utf-8",
    )

    def fake_download_one_day(**kwargs: object) -> dict[str, object]:
        raise AssertionError("failed-day summaries should be left for the repair pass")

    script._download_one_day = fake_download_one_day

    result = script.download_dukascopy_ticks_cli(
        symbol="EURUSD",
        trading_date=date(2026, 5, 4),
        vendor_output_dir=tmp_path / "vendor",
        bootstrap_output_dir=tmp_path / "history",
        bars_output_dir=tmp_path / "bars",
        summary_output_path=summary_path,
        timeframes=("TICK", "M1"),
        defer_incomplete_repair=True,
    )

    daily = result["daily"][0]
    assert result["skipped_date_count"] == 1
    assert daily["reason"] == "deferred_incomplete_day_repair"


def test_repair_incomplete_only_processes_marked_days(
    tmp_path: Path,
) -> None:
    script = _load_script_module("download_dukascopy_ticks")
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps({"failed_hour_count": 1}), encoding="utf-8")
    calls: list[date] = []

    def fake_download_one_day(**kwargs: object) -> dict[str, object]:
        trading_date = kwargs["trading_date"]
        assert isinstance(trading_date, date)
        calls.append(trading_date)
        return {
            "symbol": kwargs["symbol"],
            "date": trading_date.isoformat(),
            "row_count": 10,
            "byte_count": 20,
        }

    script._download_one_day = fake_download_one_day

    result = script.download_dukascopy_ticks_cli(
        symbol="EURUSD",
        trading_date=date(2026, 5, 4),
        vendor_output_dir=tmp_path / "vendor",
        bootstrap_output_dir=tmp_path / "history",
        bars_output_dir=tmp_path / "bars",
        summary_output_path=summary_path,
        timeframes=("TICK", "M1"),
        repair_incomplete_only=True,
    )

    assert calls == [date(2026, 5, 4)]
    assert result["completed_date_count"] == 1


def test_repair_incomplete_only_skips_clean_days(
    tmp_path: Path,
) -> None:
    script = _load_script_module("download_dukascopy_ticks")

    def fake_download_one_day(**kwargs: object) -> dict[str, object]:
        raise AssertionError("clean days should not run in repair-only mode")

    script._download_one_day = fake_download_one_day

    result = script.download_dukascopy_ticks_cli(
        symbol="EURUSD",
        trading_date=date(2026, 5, 4),
        vendor_output_dir=tmp_path / "vendor",
        bootstrap_output_dir=tmp_path / "history",
        bars_output_dir=tmp_path / "bars",
        summary_output_path=tmp_path / "summary.json",
        timeframes=("TICK", "M1"),
        repair_incomplete_only=True,
    )

    daily = result["daily"][0]
    assert result["skipped_date_count"] == 1
    assert daily["reason"] == "not_marked_for_repair"


def test_download_bytes_retries_remote_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module("download_dukascopy_ticks")
    calls = 0

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"ok"

    def fake_urlopen(request: object, *, timeout: float) -> FakeResponse:
        nonlocal calls
        assert timeout == 2.0
        calls += 1
        if calls == 1:
            raise script.RemoteDisconnected("closed")
        return FakeResponse()

    monkeypatch.setattr(script, "urlopen", fake_urlopen)
    monkeypatch.setattr(script.time, "sleep", lambda seconds: None)

    payload = script._download_bytes("https://example.test/EURUSD.bi5", timeout_seconds=2.0)

    assert payload == b"ok"
    assert calls == 2


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

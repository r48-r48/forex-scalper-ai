"""Download Dukascopy public historical tick data and derive QA-gated timeframes."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"
for import_path in (SCRIPT_ROOT, SRC_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from bootstrap_history import bootstrap_history_cli
from cli_utils import write_json

DUKASCOPY_BASE_URL = "https://datafeed.dukascopy.com/datafeed"
BI5_RECORD_SIZE = 20
DEFAULT_PRICE_SCALE = 100_000.0
DOWNLOAD_RETRY_COUNT = 3
DOWNLOAD_RETRY_BACKOFF_SECONDS = 0.5
DEFAULT_TIMEFRAMES: Final[tuple[str, ...]] = (
    "TICK",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "M6",
    "M10",
    "M12",
    "M15",
    "M20",
    "M30",
    "H1",
    "H2",
    "H3",
    "H4",
    "H6",
    "H8",
    "H12",
    "D1",
)
TIMEFRAME_RULES: Final[dict[str, str]] = {
    "M1": "1min",
    "M2": "2min",
    "M3": "3min",
    "M4": "4min",
    "M5": "5min",
    "M6": "6min",
    "M10": "10min",
    "M12": "12min",
    "M15": "15min",
    "M20": "20min",
    "M30": "30min",
    "H1": "1h",
    "H2": "2h",
    "H3": "3h",
    "H4": "4h",
    "H6": "6h",
    "H8": "8h",
    "H12": "12h",
    "D1": "1D",
}


@dataclass(frozen=True)
class DownloadedHour:
    """One downloaded Dukascopy hourly tick archive."""

    hour: int
    url: str
    path: Path
    byte_count: int
    sha256: str
    row_count: int
    cached: bool


@dataclass(frozen=True)
class DailyOutputPaths:
    """Resolved output paths for one UTC trading day."""

    parsed_output_path: Path
    bootstrap_output_path: Path
    summary_output_path: Path | None
    bootstrap_summary_output_path: Path
    quality_report_path: Path
    bars_output_dir: Path | None


@dataclass(frozen=True)
class HourMaterializationResult:
    """Decoded result for one hourly Dukascopy archive."""

    downloaded_hour: DownloadedHour | None
    records: tuple[dict[str, object], ...]
    failed_hour: dict[str, object] | None


class NoDukascopyDataError(RuntimeError):
    """Raised when a trading day has no public Dukascopy tick archives."""


def download_dukascopy_ticks_cli(
    *,
    symbol: str,
    trading_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    vendor_output_dir: Path,
    parsed_output_path: Path | None = None,
    bootstrap_output_path: Path | None = None,
    parsed_output_dir: Path | None = None,
    bootstrap_output_dir: Path | None = None,
    bars_output_dir: Path | None = None,
    summary_output_path: Path | None = None,
    bootstrap_summary_output_path: Path | None = None,
    quality_report_path: Path | None = None,
    base_url: str = DUKASCOPY_BASE_URL,
    price_scale: float = DEFAULT_PRICE_SCALE,
    timeout_seconds: float = 30.0,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    resume: bool = True,
    defer_incomplete_repair: bool = False,
    repair_incomplete_only: bool = False,
    compression: str = "zstd",
    day_workers: int = 1,
    hour_workers: int = 1,
) -> dict[str, object]:
    """Download Dukascopy ticks and run the bootstrap QA gate for each day."""

    resolved_symbol = _required_symbol(symbol)
    if price_scale <= 0:
        raise ValueError("price_scale must be greater than zero.")
    if day_workers < 1:
        raise ValueError("day_workers must be at least one.")
    if hour_workers < 1:
        raise ValueError("hour_workers must be at least one.")
    if defer_incomplete_repair and repair_incomplete_only:
        raise ValueError(
            "defer_incomplete_repair cannot be combined with repair_incomplete_only."
        )
    dates = _resolve_dates(
        trading_date=trading_date,
        start_date=start_date,
        end_date=end_date,
    )
    resolved_timeframes = _resolve_timeframes(timeframes)

    if day_workers == 1 or len(dates) == 1:
        daily_payloads = [
            _process_one_day_request(
                symbol=resolved_symbol,
                trading_date=current_date,
                date_count=len(dates),
                vendor_output_dir=vendor_output_dir,
                parsed_output_path=parsed_output_path,
                bootstrap_output_path=bootstrap_output_path,
                parsed_output_dir=parsed_output_dir,
                bootstrap_output_dir=bootstrap_output_dir,
                bars_output_dir=bars_output_dir,
                summary_output_path=summary_output_path,
                bootstrap_summary_output_path=bootstrap_summary_output_path,
                quality_report_path=quality_report_path,
                base_url=base_url,
                price_scale=price_scale,
                timeout_seconds=timeout_seconds,
                timeframes=resolved_timeframes,
                resume=resume,
                defer_incomplete_repair=defer_incomplete_repair,
                repair_incomplete_only=repair_incomplete_only,
                compression=compression,
                hour_workers=hour_workers,
            )
            for current_date in dates
        ]
    else:
        with ThreadPoolExecutor(max_workers=day_workers) as executor:
            daily_payloads = list(
                executor.map(
                    lambda current_date: _process_one_day_request(
                        symbol=resolved_symbol,
                        trading_date=current_date,
                        date_count=len(dates),
                        vendor_output_dir=vendor_output_dir,
                        parsed_output_path=parsed_output_path,
                        bootstrap_output_path=bootstrap_output_path,
                        parsed_output_dir=parsed_output_dir,
                        bootstrap_output_dir=bootstrap_output_dir,
                        bars_output_dir=bars_output_dir,
                        summary_output_path=summary_output_path,
                        bootstrap_summary_output_path=bootstrap_summary_output_path,
                        quality_report_path=quality_report_path,
                        base_url=base_url,
                        price_scale=price_scale,
                        timeout_seconds=timeout_seconds,
                        timeframes=resolved_timeframes,
                        resume=resume,
                        defer_incomplete_repair=defer_incomplete_repair,
                        repair_incomplete_only=repair_incomplete_only,
                        compression=compression,
                        hour_workers=hour_workers,
                    ),
                    dates,
                )
            )

    payload = _range_summary_payload(
        symbol=resolved_symbol,
        dates=dates,
        base_url=base_url,
        price_scale=price_scale,
        timeframes=resolved_timeframes,
        daily_payloads=daily_payloads,
    )
    if summary_output_path is not None and len(dates) > 1:
        write_json(payload, summary_output_path)
    return payload


def _download_one_day(
    *,
    symbol: str,
    trading_date: date,
    vendor_output_dir: Path,
    parsed_output_path: Path,
    bootstrap_output_path: Path,
    summary_output_path: Path | None,
    bootstrap_summary_output_path: Path | None,
    quality_report_path: Path | None,
    bars_output_dir: Path | None,
    base_url: str,
    price_scale: float,
    timeout_seconds: float,
    timeframes: tuple[str, ...],
    resume: bool,
    compression: str,
    hour_workers: int,
) -> dict[str, object]:
    """Download and materialize one day of Dukascopy data."""

    downloaded_hours: list[DownloadedHour] = []
    failed_hours: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    hours = tuple(range(24))
    if hour_workers == 1:
        hour_results = [
            _materialize_hour(
                base_url=base_url,
                symbol=symbol,
                trading_date=trading_date,
                hour=hour,
                vendor_output_dir=vendor_output_dir,
                timeout_seconds=timeout_seconds,
                price_scale=price_scale,
                resume=resume,
            )
            for hour in hours
        ]
    else:
        with ThreadPoolExecutor(max_workers=hour_workers) as executor:
            hour_results = list(
                executor.map(
                    lambda hour: _materialize_hour(
                        base_url=base_url,
                        symbol=symbol,
                        trading_date=trading_date,
                        hour=hour,
                        vendor_output_dir=vendor_output_dir,
                        timeout_seconds=timeout_seconds,
                        price_scale=price_scale,
                        resume=resume,
                    ),
                    hours,
                )
            )

    for result in hour_results:
        if result.downloaded_hour is not None:
            downloaded_hours.append(result.downloaded_hour)
        if result.failed_hour is not None:
            failed_hours.append(result.failed_hour)
        records.extend(result.records)

    downloaded_hours.sort(key=lambda hour: hour.hour)

    if not downloaded_hours and failed_hours:
        raise RuntimeError(
            f"All Dukascopy hourly archive downloads failed for {symbol} {trading_date}."
        )
    if not downloaded_hours:
        raise NoDukascopyDataError(
            f"No Dukascopy tick archives were downloaded for {symbol} {trading_date}."
        )
    if not records:
        raise NoDukascopyDataError(
            f"Dukascopy archives for {symbol} {trading_date} contained no ticks."
        )

    parsed_frame = _sorted_parsed_tick_frame(records)
    parsed_output_path.parent.mkdir(parents=True, exist_ok=True)
    parsed_frame.to_csv(parsed_output_path, index=False)

    dataset_id = f"dukascopy-{symbol.lower()}-ticks-{trading_date.isoformat()}"
    bootstrap_payload = bootstrap_history_cli(
        input_path=parsed_output_path,
        output_path=bootstrap_output_path,
        dataset_id=dataset_id,
        summary_output_path=bootstrap_summary_output_path,
        quality_report_path=quality_report_path,
        symbol=symbol,
        venue="DUKASCOPY",
        timeframe="tick",
        history_source="dukascopy-datafeed",
        sort_by_event_timestamp=True,
        compression=compression,
    )
    bar_outputs = _write_timeframe_bars(
        parsed_frame,
        symbol=symbol,
        trading_date=trading_date,
        bars_output_dir=bars_output_dir,
        timeframes=timeframes,
        compression=compression,
    )

    payload = _summary_payload(
        symbol=symbol,
        trading_date=trading_date,
        base_url=base_url,
        price_scale=price_scale,
        downloaded_hours=downloaded_hours,
        parsed_output_path=parsed_output_path,
        bootstrap_output_path=bootstrap_output_path,
        bootstrap_summary_output_path=bootstrap_summary_output_path,
        quality_report_path=quality_report_path,
        bootstrap_payload=bootstrap_payload,
        bar_outputs=bar_outputs,
        timeframes=timeframes,
        failed_hours=failed_hours,
    )
    write_json(payload, summary_output_path)
    return payload


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Download one day of public Dukascopy historical ticks and run QA bootstrap.",
    )
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--date", default=None, help="UTC trading date in YYYY-MM-DD format.")
    parser.add_argument("--start-date", default=None, help="Inclusive UTC start date.")
    parser.add_argument("--end-date", default=None, help="Inclusive UTC end date.")
    parser.add_argument(
        "--vendor-output-dir",
        type=Path,
        default=Path("data/raw/vendor/dukascopy"),
        help="Root directory for downloaded hourly .bi5 files.",
    )
    parser.add_argument(
        "--parsed-output-path",
        type=Path,
        default=None,
        help="Single-day parsed tick CSV path. Defaults under data/raw/vendor/dukascopy.",
    )
    parser.add_argument(
        "--bootstrap-output-path",
        type=Path,
        default=None,
        help="Single-day QA-gated Parquet/CSV output path. Defaults under data/raw/history.",
    )
    parser.add_argument(
        "--parsed-output-dir",
        type=Path,
        default=Path("data/raw/vendor/dukascopy/parsed"),
    )
    parser.add_argument(
        "--bootstrap-output-dir",
        type=Path,
        default=Path("data/raw/history/dukascopy"),
    )
    parser.add_argument(
        "--bars-output-dir",
        type=Path,
        default=Path("data/processed/bars/dukascopy"),
    )
    parser.add_argument("--summary-output-path", type=Path, default=None)
    parser.add_argument("--bootstrap-summary-output-path", type=Path, default=None)
    parser.add_argument("--quality-report-path", type=Path, default=None)
    parser.add_argument("--base-url", default=DUKASCOPY_BASE_URL)
    parser.add_argument("--price-scale", type=float, default=DEFAULT_PRICE_SCALE)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--timeframes",
        default=",".join(DEFAULT_TIMEFRAMES),
        help="Comma-separated timeframes to materialize; include TICK for raw tick output.",
    )
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--defer-incomplete-repair",
        action="store_true",
        help=(
            "During resume, skip days already marked with failed hours or failed-day "
            "summaries so a coverage pass can continue to later years first."
        ),
    )
    parser.add_argument(
        "--repair-incomplete-only",
        action="store_true",
        help=(
            "Process only days previously marked with failed hours or failed-day "
            "summaries; useful after a coverage-first pass."
        ),
    )
    parser.add_argument(
        "--compression",
        default="zstd",
        help="Parquet compression codec, or 'none'. Ignored for CSV output.",
    )
    parser.add_argument(
        "--day-workers",
        type=int,
        default=1,
        help="Number of UTC days to process concurrently for large resumable ranges.",
    )
    parser.add_argument(
        "--hour-workers",
        type=int,
        default=1,
        help="Number of hourly archives to download concurrently inside each day.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    trading_date = None if args.date is None else date.fromisoformat(args.date)
    start_date = None if args.start_date is None else date.fromisoformat(args.start_date)
    end_date = None if args.end_date is None else date.fromisoformat(args.end_date)
    symbol = _required_symbol(args.symbol)
    payload = download_dukascopy_ticks_cli(
        symbol=symbol,
        trading_date=trading_date,
        start_date=start_date,
        end_date=end_date,
        vendor_output_dir=args.vendor_output_dir,
        parsed_output_path=args.parsed_output_path,
        bootstrap_output_path=args.bootstrap_output_path,
        parsed_output_dir=args.parsed_output_dir,
        bootstrap_output_dir=args.bootstrap_output_dir,
        bars_output_dir=args.bars_output_dir,
        summary_output_path=args.summary_output_path,
        bootstrap_summary_output_path=args.bootstrap_summary_output_path,
        quality_report_path=args.quality_report_path,
        base_url=args.base_url,
        price_scale=args.price_scale,
        timeout_seconds=args.timeout_seconds,
        timeframes=tuple(args.timeframes.split(",")),
        resume=not args.no_resume,
        defer_incomplete_repair=args.defer_incomplete_repair,
        repair_incomplete_only=args.repair_incomplete_only,
        compression=args.compression,
        day_workers=args.day_workers,
        hour_workers=args.hour_workers,
    )
    print(write_json(_console_payload(payload), None))


def _process_one_day_request(
    *,
    symbol: str,
    trading_date: date,
    date_count: int,
    vendor_output_dir: Path,
    parsed_output_path: Path | None,
    bootstrap_output_path: Path | None,
    parsed_output_dir: Path | None,
    bootstrap_output_dir: Path | None,
    bars_output_dir: Path | None,
    summary_output_path: Path | None,
    bootstrap_summary_output_path: Path | None,
    quality_report_path: Path | None,
    base_url: str,
    price_scale: float,
    timeout_seconds: float,
    timeframes: tuple[str, ...],
    resume: bool,
    defer_incomplete_repair: bool,
    repair_incomplete_only: bool,
    compression: str,
    hour_workers: int,
) -> dict[str, object]:
    resolved_paths = _daily_paths(
        symbol=symbol,
        trading_date=trading_date,
        vendor_output_dir=vendor_output_dir,
        parsed_output_path=parsed_output_path,
        bootstrap_output_path=bootstrap_output_path,
        parsed_output_dir=parsed_output_dir,
        bootstrap_output_dir=bootstrap_output_dir,
        bars_output_dir=bars_output_dir,
        summary_output_path=summary_output_path,
        bootstrap_summary_output_path=bootstrap_summary_output_path,
        quality_report_path=quality_report_path,
        multiple_days=date_count > 1,
    )
    repair_needed = _daily_incomplete_repair_needed(paths=resolved_paths)
    if repair_incomplete_only and not repair_needed:
        return {
            "symbol": symbol,
            "date": trading_date.isoformat(),
            "skipped": True,
            "reason": "not_marked_for_repair",
            "message": "no previous incomplete-day evidence was found",
        }
    if resume and defer_incomplete_repair and repair_needed:
        return {
            "symbol": symbol,
            "date": trading_date.isoformat(),
            "skipped": True,
            "reason": "deferred_incomplete_day_repair",
            "message": "previous incomplete-day evidence was left for repair pass",
        }
    if resume and _daily_outputs_complete(
        paths=resolved_paths,
        symbol=symbol,
        trading_date=trading_date,
        timeframes=timeframes,
    ):
        return {
            "symbol": symbol,
            "date": trading_date.isoformat(),
            "skipped": True,
            "reason": "outputs_exist",
            "bootstrap_output_path": str(resolved_paths.bootstrap_output_path),
        }
    if resume and _daily_no_data_complete(paths=resolved_paths):
        return {
            "symbol": symbol,
            "date": trading_date.isoformat(),
            "skipped": True,
            "reason": "no_data_available",
            "message": "previous no-data summary exists",
        }
    try:
        return _download_one_day(
            symbol=symbol,
            trading_date=trading_date,
            vendor_output_dir=vendor_output_dir,
            parsed_output_path=resolved_paths.parsed_output_path,
            bootstrap_output_path=resolved_paths.bootstrap_output_path,
            summary_output_path=resolved_paths.summary_output_path,
            bootstrap_summary_output_path=resolved_paths.bootstrap_summary_output_path,
            quality_report_path=resolved_paths.quality_report_path,
            bars_output_dir=resolved_paths.bars_output_dir,
            base_url=base_url,
            price_scale=price_scale,
            timeout_seconds=timeout_seconds,
            timeframes=timeframes,
            resume=resume,
            compression=compression,
            hour_workers=hour_workers,
        )
    except NoDukascopyDataError as exc:
        if date_count == 1:
            raise
        return _skipped_day_payload(
            symbol=symbol,
            trading_date=trading_date,
            reason="no_data_available",
            exc=exc,
            summary_output_path=resolved_paths.summary_output_path,
        )
    except ValueError as exc:
        if date_count == 1:
            raise
        return _skipped_day_payload(
            symbol=symbol,
            trading_date=trading_date,
            reason="day_materialization_failed",
            exc=exc,
            summary_output_path=resolved_paths.summary_output_path,
        )
    except RuntimeError as exc:
        if date_count == 1:
            raise
        return _skipped_day_payload(
            symbol=symbol,
            trading_date=trading_date,
            reason="day_download_failed",
            exc=exc,
            summary_output_path=resolved_paths.summary_output_path,
        )


def _skipped_day_payload(
    *,
    symbol: str,
    trading_date: date,
    reason: str,
    exc: Exception,
    summary_output_path: Path | None,
) -> dict[str, object]:
    payload = {
        "symbol": symbol,
        "date": trading_date.isoformat(),
        "skipped": True,
        "reason": reason,
        "message": str(exc),
    }
    write_json(payload, summary_output_path)
    return payload


def _resolve_dates(
    *,
    trading_date: date | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, ...]:
    if trading_date is not None and (start_date is not None or end_date is not None):
        raise ValueError("--date cannot be combined with --start-date/--end-date.")
    if trading_date is not None:
        return (trading_date,)
    if start_date is None or end_date is None:
        raise ValueError("Provide either --date or both --start-date and --end-date.")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date.")
    day_count = (end_date - start_date).days + 1
    return tuple(start_date + timedelta(days=offset) for offset in range(day_count))


def _resolve_timeframes(timeframes: tuple[str, ...]) -> tuple[str, ...]:
    resolved: list[str] = []
    for timeframe in timeframes:
        normalized = timeframe.strip().upper()
        if not normalized:
            continue
        if normalized != "TICK" and normalized not in TIMEFRAME_RULES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        resolved.append(normalized)
    if not resolved:
        raise ValueError("At least one timeframe must be provided.")
    return tuple(dict.fromkeys(resolved))


def _daily_paths(
    *,
    symbol: str,
    trading_date: date,
    vendor_output_dir: Path,
    parsed_output_path: Path | None,
    bootstrap_output_path: Path | None,
    parsed_output_dir: Path | None,
    bootstrap_output_dir: Path | None,
    bars_output_dir: Path | None,
    summary_output_path: Path | None,
    bootstrap_summary_output_path: Path | None,
    quality_report_path: Path | None,
    multiple_days: bool,
) -> DailyOutputPaths:
    if multiple_days and (
        parsed_output_path is not None
        or bootstrap_output_path is not None
        or bootstrap_summary_output_path is not None
        or quality_report_path is not None
    ):
        raise ValueError(
            "Single-file output paths cannot be used for multi-day downloads; use output dirs."
        )
    parsed_root = parsed_output_dir or (vendor_output_dir / "parsed")
    bootstrap_root = bootstrap_output_dir or Path("data/raw/history/dukascopy")
    artifact_root = Path("data/artifacts/dukascopy") / symbol / trading_date.isoformat()
    return DailyOutputPaths(
        parsed_output_path=(
            parsed_output_path
            or parsed_root
            / symbol
            / f"{symbol}-{trading_date.isoformat()}-ticks.csv"
        ),
        bootstrap_output_path=(
            bootstrap_output_path
            or bootstrap_root
            / symbol
            / "TICK"
            / f"date={trading_date.isoformat()}.parquet"
        ),
        summary_output_path=(
            artifact_root / "download-summary.json" if multiple_days else summary_output_path
        ),
        bootstrap_summary_output_path=(
            bootstrap_summary_output_path or artifact_root / "bootstrap-summary.json"
        ),
        quality_report_path=quality_report_path or artifact_root / "quality-report.json",
        bars_output_dir=bars_output_dir,
    )


def _daily_outputs_complete(
    *,
    paths: DailyOutputPaths,
    symbol: str,
    trading_date: date,
    timeframes: tuple[str, ...],
) -> bool:
    if not paths.bootstrap_output_path.exists():
        return False
    if _daily_has_failed_hours(paths=paths):
        return False
    if paths.bars_output_dir is None:
        return True
    for timeframe in timeframes:
        if timeframe == "TICK":
            continue
        output_path = (
            paths.bars_output_dir
            / symbol
            / timeframe
            / f"date={trading_date.isoformat()}.parquet"
        )
        if not output_path.exists():
            return False
    return True


def _daily_no_data_complete(*, paths: DailyOutputPaths) -> bool:
    if paths.summary_output_path is None or not paths.summary_output_path.exists():
        return False
    try:
        payload = json.loads(paths.summary_output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("skipped")) and payload.get("reason") == "no_data_available"


def _daily_has_failed_hours(*, paths: DailyOutputPaths) -> bool:
    if paths.summary_output_path is None or not paths.summary_output_path.exists():
        return False
    try:
        payload = json.loads(paths.summary_output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return int(payload.get("failed_hour_count", 0)) > 0


def _daily_has_failed_day_summary(*, paths: DailyOutputPaths) -> bool:
    if paths.summary_output_path is None or not paths.summary_output_path.exists():
        return False
    try:
        payload = json.loads(paths.summary_output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    repair_reasons = {"day_download_failed", "day_materialization_failed"}
    return bool(payload.get("skipped")) and payload.get("reason") in repair_reasons


def _daily_incomplete_repair_needed(*, paths: DailyOutputPaths) -> bool:
    return _daily_has_failed_hours(paths=paths) or _daily_has_failed_day_summary(paths=paths)


def _dukascopy_hour_url(
    *,
    base_url: str,
    symbol: str,
    trading_date: date,
    hour: int,
) -> str:
    month_index = trading_date.month - 1
    return (
        f"{base_url.rstrip('/')}/{symbol}/{trading_date.year:04d}/{month_index:02d}/"
        f"{trading_date.day:02d}/{hour:02d}h_ticks.bi5"
    )


def _download_bytes(url: str, *, timeout_seconds: float) -> bytes | None:
    request = Request(url, headers={"User-Agent": "forex-scalper-ai/0.1"})
    for attempt in range(DOWNLOAD_RETRY_COUNT + 1):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt >= DOWNLOAD_RETRY_COUNT:
                raise RuntimeError(f"Failed to download {url}: HTTP {exc.code}") from exc
        except URLError as exc:
            if attempt >= DOWNLOAD_RETRY_COUNT:
                raise RuntimeError(f"Failed to download {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            if attempt >= DOWNLOAD_RETRY_COUNT:
                raise RuntimeError(f"Failed to download {url}: timed out") from exc
        time.sleep(DOWNLOAD_RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise RuntimeError(f"Failed to download {url}.")


def _materialize_hour(
    *,
    base_url: str,
    symbol: str,
    trading_date: date,
    hour: int,
    vendor_output_dir: Path,
    timeout_seconds: float,
    price_scale: float,
    resume: bool,
) -> HourMaterializationResult:
    url = _dukascopy_hour_url(
        base_url=base_url,
        symbol=symbol,
        trading_date=trading_date,
        hour=hour,
    )
    try:
        raw_payload, cached = _load_or_download_hour_payload(
            url=url,
            vendor_output_dir=vendor_output_dir,
            symbol=symbol,
            trading_date=trading_date,
            hour=hour,
            timeout_seconds=timeout_seconds,
            resume=resume,
        )
    except RuntimeError as exc:
        hour_path = _hour_payload_path(
            vendor_output_dir=vendor_output_dir,
            symbol=symbol,
            trading_date=trading_date,
            hour=hour,
        )
        return HourMaterializationResult(
            downloaded_hour=None,
            records=(),
            failed_hour={
                "hour": hour,
                "url": url,
                "path": str(hour_path),
                "message": str(exc),
            },
        )
    if raw_payload is None:
        return HourMaterializationResult(
            downloaded_hour=None,
            records=(),
            failed_hour=None,
        )

    hour_path = _hour_payload_path(
        vendor_output_dir=vendor_output_dir,
        symbol=symbol,
        trading_date=trading_date,
        hour=hour,
    )
    try:
        hour_records = _parse_bi5_ticks(
            raw_payload,
            symbol=symbol,
            trading_date=trading_date,
            hour=hour,
            price_scale=price_scale,
        )
    except ValueError as exc:
        hour_path.unlink(missing_ok=True)
        return HourMaterializationResult(
            downloaded_hour=None,
            records=(),
            failed_hour={
                "hour": hour,
                "url": url,
                "path": str(hour_path),
                "message": str(exc),
            },
        )

    return HourMaterializationResult(
        downloaded_hour=DownloadedHour(
            hour=hour,
            url=url,
            path=hour_path,
            byte_count=len(raw_payload),
            sha256=hashlib.sha256(raw_payload).hexdigest(),
            row_count=len(hour_records),
            cached=cached,
        ),
        records=tuple(hour_records),
        failed_hour=None,
    )


def _hour_payload_path(
    *,
    vendor_output_dir: Path,
    symbol: str,
    trading_date: date,
    hour: int,
) -> Path:
    return vendor_output_dir / symbol / trading_date.isoformat() / f"{hour:02d}h_ticks.bi5"


def _load_or_download_hour_payload(
    *,
    url: str,
    vendor_output_dir: Path,
    symbol: str,
    trading_date: date,
    hour: int,
    timeout_seconds: float,
    resume: bool,
) -> tuple[bytes | None, bool]:
    output_path = _hour_payload_path(
        vendor_output_dir=vendor_output_dir,
        symbol=symbol,
        trading_date=trading_date,
        hour=hour,
    )
    if resume and output_path.exists():
        return output_path.read_bytes(), True
    payload = _download_bytes(url, timeout_seconds=timeout_seconds)
    if payload is None:
        return None, False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    return payload, False


def _parse_bi5_ticks(
    payload: bytes,
    *,
    symbol: str,
    trading_date: date,
    hour: int,
    price_scale: float,
) -> list[dict[str, object]]:
    try:
        decompressed = lzma.decompress(payload)
    except lzma.LZMAError as exc:
        raise ValueError("Dukascopy .bi5 payload could not be decompressed.") from exc
    if len(decompressed) % BI5_RECORD_SIZE != 0:
        raise ValueError(
            "Dukascopy .bi5 payload size is not a multiple of the expected tick record size."
        )

    hour_start = datetime(
        trading_date.year,
        trading_date.month,
        trading_date.day,
        hour,
        tzinfo=UTC,
    )
    records: list[dict[str, object]] = []
    for sequence, offset in enumerate(range(0, len(decompressed), BI5_RECORD_SIZE)):
        milliseconds, ask_raw, bid_raw, ask_size, bid_size = struct.unpack(
            ">IIIff",
            decompressed[offset : offset + BI5_RECORD_SIZE],
        )
        event_timestamp = hour_start + timedelta(milliseconds=milliseconds)
        records.append(
            {
                "symbol": symbol,
                "event_timestamp": event_timestamp.isoformat().replace("+00:00", "Z"),
                "bid": bid_raw / price_scale,
                "ask": ask_raw / price_scale,
                "bid_size": float(bid_size),
                "ask_size": float(ask_size),
                "sequence": hour * 10_000_000 + sequence,
                "source": "dukascopy-datafeed",
            }
        )
    return records


def _sorted_parsed_tick_frame(records: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(records)
    frame["_sort_event_timestamp"] = pd.to_datetime(
        frame["event_timestamp"],
        utc=True,
        format="mixed",
    )
    sorted_frame = frame.sort_values(
        by=["_sort_event_timestamp", "sequence"],
        kind="mergesort",
    )
    return sorted_frame.drop(columns=["_sort_event_timestamp"]).reset_index(drop=True)


def _write_timeframe_bars(
    frame: pd.DataFrame,
    *,
    symbol: str,
    trading_date: date,
    bars_output_dir: Path | None,
    timeframes: tuple[str, ...],
    compression: str,
) -> list[dict[str, object]]:
    if bars_output_dir is None:
        return []

    tick_frame = frame.copy()
    tick_frame["event_timestamp"] = pd.to_datetime(
        tick_frame["event_timestamp"],
        utc=True,
        format="mixed",
    )
    tick_frame["mid_price"] = (tick_frame["bid"] + tick_frame["ask"]) / 2.0
    tick_frame = tick_frame.set_index("event_timestamp").sort_index()

    requested_timeframes = tuple(timeframe for timeframe in timeframes if timeframe != "TICK")
    if not requested_timeframes:
        return []
    m1_frame = _build_timeframe_bars_from_ticks(tick_frame, timeframe="M1")
    if m1_frame.empty:
        return []

    outputs: list[dict[str, object]] = []
    for timeframe in requested_timeframes:
        bar_frame = (
            m1_frame
            if timeframe == "M1"
            else _build_timeframe_bars_from_m1(m1_frame, timeframe=timeframe)
        )
        if bar_frame.empty:
            continue
        output_path = (
            bars_output_dir
            / symbol
            / timeframe
            / f"date={trading_date.isoformat()}.parquet"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        bar_frame.to_parquet(
            output_path,
            index=False,
            compression=None if compression == "none" else compression,
        )
        outputs.append(
            {
                "timeframe": timeframe,
                "path": str(output_path),
                "row_count": len(bar_frame),
            }
        )
    return outputs


def _build_timeframe_bars_from_ticks(
    frame: pd.DataFrame,
    *,
    timeframe: str,
) -> pd.DataFrame:
    rule = TIMEFRAME_RULES[timeframe]
    grouped = frame.resample(rule, label="left", closed="left")
    bar_frame = grouped.agg(
        bid_open=("bid", "first"),
        bid_high=("bid", "max"),
        bid_low=("bid", "min"),
        bid_close=("bid", "last"),
        ask_open=("ask", "first"),
        ask_high=("ask", "max"),
        ask_low=("ask", "min"),
        ask_close=("ask", "last"),
        open=("mid_price", "first"),
        high=("mid_price", "max"),
        low=("mid_price", "min"),
        close=("mid_price", "last"),
        bid_size=("bid_size", "sum"),
        ask_size=("ask_size", "sum"),
        tick_count=("mid_price", "count"),
    )
    bar_frame = bar_frame[bar_frame["tick_count"] > 0].reset_index()
    if bar_frame.empty:
        return bar_frame
    bar_frame["event_timestamp"] = bar_frame["event_timestamp"].dt.tz_convert("UTC")
    bar_frame["available_timestamp"] = bar_frame["event_timestamp"] + pd.to_timedelta(rule)
    bar_frame["symbol"] = str(frame["symbol"].iloc[0])
    bar_frame["venue"] = "DUKASCOPY"
    bar_frame["timeframe"] = timeframe
    bar_frame["source"] = "dukascopy-datafeed"
    bar_frame["spread_open"] = bar_frame["ask_open"] - bar_frame["bid_open"]
    bar_frame["spread_close"] = bar_frame["ask_close"] - bar_frame["bid_close"]
    bar_frame["mid_return"] = bar_frame["close"].pct_change().fillna(0.0)
    ordered_columns = [
        "symbol",
        "venue",
        "timeframe",
        "event_timestamp",
        "available_timestamp",
        "open",
        "high",
        "low",
        "close",
        "mid_return",
        "bid_open",
        "bid_high",
        "bid_low",
        "bid_close",
        "ask_open",
        "ask_high",
        "ask_low",
        "ask_close",
        "spread_open",
        "spread_close",
        "bid_size",
        "ask_size",
        "tick_count",
        "source",
    ]
    return bar_frame.loc[:, ordered_columns]


def _build_timeframe_bars_from_m1(
    frame: pd.DataFrame,
    *,
    timeframe: str,
) -> pd.DataFrame:
    rule = TIMEFRAME_RULES[timeframe]
    m1_frame = frame.copy()
    m1_frame["event_timestamp"] = pd.to_datetime(
        m1_frame["event_timestamp"],
        utc=True,
        format="mixed",
    )
    m1_frame = m1_frame.set_index("event_timestamp").sort_index()
    grouped = m1_frame.resample(rule, label="left", closed="left")
    bar_frame = grouped.agg(
        bid_open=("bid_open", "first"),
        bid_high=("bid_high", "max"),
        bid_low=("bid_low", "min"),
        bid_close=("bid_close", "last"),
        ask_open=("ask_open", "first"),
        ask_high=("ask_high", "max"),
        ask_low=("ask_low", "min"),
        ask_close=("ask_close", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        bid_size=("bid_size", "sum"),
        ask_size=("ask_size", "sum"),
        tick_count=("tick_count", "sum"),
    )
    bar_frame = bar_frame[bar_frame["tick_count"] > 0].reset_index()
    if bar_frame.empty:
        return bar_frame
    bar_frame["event_timestamp"] = bar_frame["event_timestamp"].dt.tz_convert("UTC")
    bar_frame["available_timestamp"] = bar_frame["event_timestamp"] + pd.to_timedelta(rule)
    bar_frame["symbol"] = str(frame["symbol"].iloc[0])
    bar_frame["venue"] = "DUKASCOPY"
    bar_frame["timeframe"] = timeframe
    bar_frame["source"] = "dukascopy-datafeed"
    bar_frame["spread_open"] = bar_frame["ask_open"] - bar_frame["bid_open"]
    bar_frame["spread_close"] = bar_frame["ask_close"] - bar_frame["bid_close"]
    bar_frame["mid_return"] = bar_frame["close"].pct_change().fillna(0.0)
    return bar_frame.loc[:, _BAR_OUTPUT_COLUMNS]


_BAR_OUTPUT_COLUMNS: Final[list[str]] = [
    "symbol",
    "venue",
    "timeframe",
    "event_timestamp",
    "available_timestamp",
    "open",
    "high",
    "low",
    "close",
    "mid_return",
    "bid_open",
    "bid_high",
    "bid_low",
    "bid_close",
    "ask_open",
    "ask_high",
    "ask_low",
    "ask_close",
    "spread_open",
    "spread_close",
    "bid_size",
    "ask_size",
    "tick_count",
    "source",
]


def _summary_payload(
    *,
    symbol: str,
    trading_date: date,
    base_url: str,
    price_scale: float,
    downloaded_hours: list[DownloadedHour],
    parsed_output_path: Path,
    bootstrap_output_path: Path,
    bootstrap_summary_output_path: Path | None,
    quality_report_path: Path | None,
    bootstrap_payload: dict[str, object],
    bar_outputs: list[dict[str, object]],
    timeframes: tuple[str, ...],
    failed_hours: list[dict[str, object]],
) -> dict[str, object]:
    row_count = sum(hour.row_count for hour in downloaded_hours)
    byte_count = sum(hour.byte_count for hour in downloaded_hours)
    return {
        "created_at": datetime.now(UTC),
        "source": {
            "name": "Dukascopy Historical Data Feed",
            "base_url": base_url,
            "format": "hourly .bi5 LZMA tick archives",
            "timezone": "UTC",
        },
        "symbol": symbol,
        "date": trading_date.isoformat(),
        "price_scale": price_scale,
        "downloaded_hour_count": len(downloaded_hours),
        "failed_hour_count": len(failed_hours),
        "row_count": row_count,
        "byte_count": byte_count,
        "downloaded_hours": [
            {
                "hour": hour.hour,
                "url": hour.url,
                "path": str(hour.path),
                "byte_count": hour.byte_count,
                "sha256": hour.sha256,
                "row_count": hour.row_count,
                "cached": hour.cached,
            }
            for hour in downloaded_hours
        ],
        "parsed_output_path": str(parsed_output_path),
        "failed_hours": failed_hours,
        "bootstrap_output_path": str(bootstrap_output_path),
        "bootstrap_summary_output_path": (
            None if bootstrap_summary_output_path is None else str(bootstrap_summary_output_path)
        ),
        "quality_report_path": None if quality_report_path is None else str(quality_report_path),
        "quality": bootstrap_payload["quality"],
        "timeframes": list(timeframes),
        "bar_outputs": bar_outputs,
        "safety": {
            "offline_only": True,
            "broker_orders_submitted": False,
            "order_send_called": False,
        },
    }


def _range_summary_payload(
    *,
    symbol: str,
    dates: tuple[date, ...],
    base_url: str,
    price_scale: float,
    timeframes: tuple[str, ...],
    daily_payloads: list[dict[str, object]],
) -> dict[str, object]:
    completed_payloads = [payload for payload in daily_payloads if not payload.get("skipped")]
    skipped_payloads = [payload for payload in daily_payloads if payload.get("skipped")]
    return {
        "created_at": datetime.now(UTC),
        "source": {
            "name": "Dukascopy Historical Data Feed",
            "base_url": base_url,
            "format": "hourly .bi5 LZMA tick archives",
            "timezone": "UTC",
        },
        "symbol": symbol,
        "start_date": dates[0].isoformat(),
        "end_date": dates[-1].isoformat(),
        "date_count": len(dates),
        "completed_date_count": len(completed_payloads),
        "skipped_date_count": len(skipped_payloads),
        "price_scale": price_scale,
        "timeframes": list(timeframes),
        "row_count": sum(int(payload.get("row_count", 0)) for payload in completed_payloads),
        "byte_count": sum(int(payload.get("byte_count", 0)) for payload in completed_payloads),
        "daily": daily_payloads,
        "safety": {
            "offline_only": True,
            "broker_orders_submitted": False,
            "order_send_called": False,
        },
    }


def _console_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return a compact payload for stdout while full details stay in summary files."""

    daily_payloads = payload.get("daily")
    if not isinstance(daily_payloads, list):
        return payload
    reason_counts: dict[str, int] = {}
    for daily_payload in daily_payloads:
        if not isinstance(daily_payload, dict):
            continue
        reason = str(daily_payload.get("reason", "completed"))
        if not daily_payload.get("skipped"):
            reason = "completed"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    compact = dict(payload)
    compact["daily"] = {
        "reason_counts": reason_counts,
        "first": [_compact_daily_payload(item) for item in daily_payloads[:3]],
        "last": (
            [_compact_daily_payload(item) for item in daily_payloads[-3:]]
            if len(daily_payloads) > 3
            else []
        ),
    }
    return compact


def _compact_daily_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    compact_keys = (
        "symbol",
        "date",
        "skipped",
        "reason",
        "row_count",
        "byte_count",
        "downloaded_hour_count",
        "bootstrap_output_path",
        "quality_report_path",
        "message",
    )
    compact = {key: payload[key] for key in compact_keys if key in payload}
    bar_outputs = payload.get("bar_outputs")
    if isinstance(bar_outputs, list):
        compact["bar_output_count"] = len(bar_outputs)
    return compact


def _required_symbol(symbol: str) -> str:
    text = symbol.strip().upper()
    if not text:
        raise ValueError("symbol must be non-empty.")
    return text


if __name__ == "__main__":
    main()

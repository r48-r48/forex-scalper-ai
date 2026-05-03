"""Bootstrap broker/vendor historical tick-like data into a QA-gated raw artifact."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"
for import_path in (SCRIPT_ROOT, SRC_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from cli_utils import dataframe_records, load_frame, write_frame, write_json

from scalper_ai.data import TickDataQualityConfig, validate_tick_data

DEFAULT_SYMBOL_COLUMN = "symbol"
DEFAULT_VENUE_COLUMN = "venue"
DEFAULT_EVENT_TIMESTAMP_COLUMN = "event_timestamp"
DEFAULT_RECEIVED_TIMESTAMP_COLUMN = "received_timestamp"
DEFAULT_BID_COLUMN = "bid"
DEFAULT_ASK_COLUMN = "ask"
DEFAULT_MID_PRICE_COLUMN = "mid_price"
OPTIONAL_COLUMN_MAP = {
    "bid_size": "bid_size",
    "ask_size": "ask_size",
    "last_price": "last_price",
    "last_size": "last_size",
    "sequence": "sequence",
    "source": "source",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "tick_volume": "tick_volume",
    "real_volume": "real_volume",
    "spread": "spread",
}


def bootstrap_history_cli(
    *,
    input_path: Path,
    output_path: Path,
    dataset_id: str,
    summary_output_path: Path | None = None,
    quality_report_path: Path | None = None,
    symbol: str | None = None,
    venue: str | None = None,
    timeframe: str | None = None,
    history_source: str = "file",
    event_timestamp_column: str = DEFAULT_EVENT_TIMESTAMP_COLUMN,
    received_timestamp_column: str | None = None,
    symbol_column: str = DEFAULT_SYMBOL_COLUMN,
    venue_column: str = DEFAULT_VENUE_COLUMN,
    bid_column: str = DEFAULT_BID_COLUMN,
    ask_column: str = DEFAULT_ASK_COLUMN,
    mid_price_column: str | None = DEFAULT_MID_PRICE_COLUMN,
    synthetic_spread_bps: float | None = None,
    max_event_gap_seconds: float | None = None,
    max_received_event_lag_seconds: float | None = None,
    group_temporal_checks_by_symbol: bool = True,
    sort_by_event_timestamp: bool = False,
    allow_quality_errors: bool = False,
    compression: str = "zstd",
) -> dict[str, object]:
    """Normalize history rows, persist QA evidence, and write a feature-ready raw frame."""

    resolved_dataset_id = _required_text(dataset_id, "dataset_id")
    timestamp_columns = _timestamp_columns(
        input_path=input_path,
        event_timestamp_column=event_timestamp_column,
        received_timestamp_column=received_timestamp_column,
    )
    source_frame = load_frame(input_path, timestamp_columns=timestamp_columns)
    normalized_frame, assumptions = _normalize_history_frame(
        source_frame,
        input_path=input_path,
        dataset_id=resolved_dataset_id,
        symbol=symbol,
        venue=venue,
        timeframe=timeframe,
        history_source=history_source,
        event_timestamp_column=event_timestamp_column,
        received_timestamp_column=received_timestamp_column,
        symbol_column=symbol_column,
        venue_column=venue_column,
        bid_column=bid_column,
        ask_column=ask_column,
        mid_price_column=mid_price_column,
        synthetic_spread_bps=synthetic_spread_bps,
    )
    quality_report = validate_tick_data(
        normalized_frame.to_dict(orient="records"),
        dataset_name=resolved_dataset_id,
        config=TickDataQualityConfig(
            max_event_gap=_seconds_to_timedelta(max_event_gap_seconds, "max_event_gap_seconds"),
            max_received_event_lag=_seconds_to_timedelta(
                max_received_event_lag_seconds,
                "max_received_event_lag_seconds",
            ),
            group_temporal_checks_by_symbol=group_temporal_checks_by_symbol,
        ),
    )
    quality_payload = quality_report.to_record()
    write_json(quality_payload, quality_report_path)

    output_written = False
    output_frame = normalized_frame
    if sort_by_event_timestamp:
        output_frame = output_frame.sort_values(
            by=["symbol", "venue", "event_timestamp"],
            kind="mergesort",
        ).reset_index(drop=True)
    if quality_report.passed or allow_quality_errors:
        write_frame(output_frame, output_path, compression=compression)
        output_written = True

    payload = _summary_payload(
        input_path=input_path,
        output_path=output_path,
        output_written=output_written,
        dataset_id=resolved_dataset_id,
        timeframe=timeframe,
        history_source=history_source,
        normalized_frame=output_frame,
        assumptions=assumptions,
        quality_payload=quality_payload,
        quality_report_path=quality_report_path,
        sort_by_event_timestamp=sort_by_event_timestamp,
    )
    write_json(payload, summary_output_path)
    if not quality_report.passed and not allow_quality_errors:
        raise ValueError(
            "Historical data quality validation failed with "
            f"{quality_report.error_count} error(s). "
            "Inspect the quality report or rerun with --allow-quality-errors to write anyway."
        )
    return payload


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Normalize broker/vendor historical CSV/Parquet rows into a feature-ready "
            "tick-like raw artifact and persist data-quality evidence."
        ),
    )
    parser.add_argument("--input-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--summary-output-path", type=Path, default=None)
    parser.add_argument("--quality-report-path", type=Path, default=None)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--venue", default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--history-source", default="file")
    parser.add_argument("--event-timestamp-column", default=DEFAULT_EVENT_TIMESTAMP_COLUMN)
    parser.add_argument("--received-timestamp-column", default=None)
    parser.add_argument("--symbol-column", default=DEFAULT_SYMBOL_COLUMN)
    parser.add_argument("--venue-column", default=DEFAULT_VENUE_COLUMN)
    parser.add_argument("--bid-column", default=DEFAULT_BID_COLUMN)
    parser.add_argument("--ask-column", default=DEFAULT_ASK_COLUMN)
    parser.add_argument("--mid-price-column", default=DEFAULT_MID_PRICE_COLUMN)
    parser.add_argument(
        "--synthetic-spread-bps",
        type=float,
        default=None,
        help=(
            "Explicit spread assumption used only when bid/ask columns are absent. "
            "No spread is synthesized unless this value is provided."
        ),
    )
    parser.add_argument("--max-event-gap-seconds", type=float, default=None)
    parser.add_argument("--max-received-event-lag-seconds", type=float, default=None)
    parser.add_argument(
        "--global-temporal-checks",
        action="store_true",
        help="Validate event ordering globally instead of grouping by symbol.",
    )
    parser.add_argument("--sort-by-event-timestamp", action="store_true")
    parser.add_argument("--allow-quality-errors", action="store_true")
    parser.add_argument(
        "--compression",
        default="zstd",
        help="Parquet compression codec, or 'none'. Ignored for CSV output.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    payload = bootstrap_history_cli(
        input_path=args.input_path,
        output_path=args.output_path,
        dataset_id=args.dataset_id,
        summary_output_path=args.summary_output_path,
        quality_report_path=args.quality_report_path,
        symbol=args.symbol,
        venue=args.venue,
        timeframe=args.timeframe,
        history_source=args.history_source,
        event_timestamp_column=args.event_timestamp_column,
        received_timestamp_column=args.received_timestamp_column,
        symbol_column=args.symbol_column,
        venue_column=args.venue_column,
        bid_column=args.bid_column,
        ask_column=args.ask_column,
        mid_price_column=args.mid_price_column,
        synthetic_spread_bps=args.synthetic_spread_bps,
        max_event_gap_seconds=args.max_event_gap_seconds,
        max_received_event_lag_seconds=args.max_received_event_lag_seconds,
        group_temporal_checks_by_symbol=not args.global_temporal_checks,
        sort_by_event_timestamp=args.sort_by_event_timestamp,
        allow_quality_errors=args.allow_quality_errors,
        compression=args.compression,
    )
    print(write_json(payload, None))


def _timestamp_columns(
    *,
    input_path: Path,
    event_timestamp_column: str,
    received_timestamp_column: str | None,
) -> tuple[str, ...]:
    columns = [_required_text(event_timestamp_column, "event_timestamp_column")]
    if received_timestamp_column is not None:
        columns.append(_required_text(received_timestamp_column, "received_timestamp_column"))
    elif input_path.suffix.lower() in {".csv", ".parquet", ".pq"}:
        columns.append(DEFAULT_RECEIVED_TIMESTAMP_COLUMN)
    return tuple(dict.fromkeys(columns))


def _normalize_history_frame(
    frame: pd.DataFrame,
    *,
    input_path: Path,
    dataset_id: str,
    symbol: str | None,
    venue: str | None,
    timeframe: str | None,
    history_source: str,
    event_timestamp_column: str,
    received_timestamp_column: str | None,
    symbol_column: str,
    venue_column: str,
    bid_column: str,
    ask_column: str,
    mid_price_column: str | None,
    synthetic_spread_bps: float | None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    _require_column(frame, event_timestamp_column)
    event_timestamp = frame[event_timestamp_column]
    received_column = received_timestamp_column or DEFAULT_RECEIVED_TIMESTAMP_COLUMN
    if received_column in frame.columns:
        received_timestamp = frame[received_column]
        received_timestamp_source = f"column:{received_column}"
    else:
        received_timestamp = event_timestamp
        received_timestamp_source = "filled_from_event_timestamp"

    symbol_values = _text_values(
        frame,
        column=symbol_column,
        fallback=symbol,
        target_name="symbol",
    )
    venue_values = _text_values(
        frame,
        column=venue_column,
        fallback=venue,
        target_name="venue",
    )
    bid, ask, bid_ask_source = _bid_ask_values(
        frame,
        bid_column=bid_column,
        ask_column=ask_column,
        mid_price_column=mid_price_column,
        synthetic_spread_bps=synthetic_spread_bps,
    )

    normalized = pd.DataFrame(
        {
            "dataset_id": dataset_id,
            "symbol": symbol_values,
            "venue": venue_values,
            "event_timestamp": event_timestamp,
            "received_timestamp": received_timestamp,
            "bid": bid,
            "ask": ask,
            "mid_price": (bid + ask) / 2.0,
            "source_path": str(input_path),
            "history_source": _required_text(history_source, "history_source"),
        },
        index=frame.index,
    )
    if timeframe is not None:
        normalized["timeframe"] = _required_text(timeframe, "timeframe")
    for source_column, target_column in OPTIONAL_COLUMN_MAP.items():
        if source_column in normalized.columns or source_column not in frame.columns:
            continue
        normalized[target_column] = frame[source_column]
    normalized["event_timestamp"] = pd.to_datetime(normalized["event_timestamp"], utc=True)
    normalized["received_timestamp"] = pd.to_datetime(normalized["received_timestamp"], utc=True)

    assumptions: dict[str, object] = {
        "received_timestamp_source": received_timestamp_source,
        "bid_ask_source": bid_ask_source,
        "synthetic_spread_bps": synthetic_spread_bps,
    }
    return normalized.reset_index(drop=True), assumptions


def _bid_ask_values(
    frame: pd.DataFrame,
    *,
    bid_column: str,
    ask_column: str,
    mid_price_column: str | None,
    synthetic_spread_bps: float | None,
) -> tuple[pd.Series, pd.Series, str]:
    if bid_column in frame.columns and ask_column in frame.columns:
        return (
            _numeric_values(frame[bid_column], column=bid_column),
            _numeric_values(frame[ask_column], column=ask_column),
            f"columns:{bid_column},{ask_column}",
        )
    if synthetic_spread_bps is None:
        raise ValueError(
            "Input frame must contain bid/ask columns or provide "
            "--mid-price-column with --synthetic-spread-bps."
        )
    if synthetic_spread_bps < 0:
        raise ValueError("synthetic_spread_bps must be non-negative when provided.")
    if mid_price_column is None:
        raise ValueError("mid_price_column is required when synthesizing bid/ask prices.")
    _require_column(frame, mid_price_column)
    mid_price = _numeric_values(frame[mid_price_column], column=mid_price_column)
    spread = mid_price * (synthetic_spread_bps / 10_000.0)
    return (
        mid_price - spread / 2.0,
        mid_price + spread / 2.0,
        f"synthetic_mid_spread:{mid_price_column}",
    )


def _summary_payload(
    *,
    input_path: Path,
    output_path: Path,
    output_written: bool,
    dataset_id: str,
    timeframe: str | None,
    history_source: str,
    normalized_frame: pd.DataFrame,
    assumptions: dict[str, object],
    quality_payload: dict[str, object],
    quality_report_path: Path | None,
    sort_by_event_timestamp: bool,
) -> dict[str, object]:
    event_timestamp = normalized_frame["event_timestamp"]
    payload: dict[str, object] = {
        "created_at": datetime.now(UTC),
        "dataset_id": dataset_id,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "output_written": output_written,
        "quality_report_path": None if quality_report_path is None else str(quality_report_path),
        "history_source": history_source,
        "timeframe": timeframe,
        "record_count": len(normalized_frame),
        "window": {
            "start": None if normalized_frame.empty else event_timestamp.min(),
            "end": None if normalized_frame.empty else event_timestamp.max(),
        },
        "symbols": sorted(str(value) for value in normalized_frame["symbol"].dropna().unique()),
        "venues": sorted(str(value) for value in normalized_frame["venue"].dropna().unique()),
        "columns": list(normalized_frame.columns),
        "assumptions": assumptions,
        "quality": {
            "passed": quality_payload["passed"],
            "issue_count": quality_payload["issue_count"],
            "error_count": quality_payload["error_count"],
            "warning_count": quality_payload["warning_count"],
            "info_count": quality_payload["info_count"],
            "max_event_gap_seconds": quality_payload["max_event_gap_seconds"],
            "max_received_event_lag_seconds": quality_payload[
                "max_received_event_lag_seconds"
            ],
        },
        "sort_by_event_timestamp": sort_by_event_timestamp,
        "safety": {
            "offline_only": True,
            "broker_orders_submitted": False,
            "order_send_called": False,
        },
        "preview": dataframe_records(normalized_frame.head(3)),
    }
    return payload


def _text_values(
    frame: pd.DataFrame,
    *,
    column: str,
    fallback: str | None,
    target_name: str,
) -> pd.Series:
    if column in frame.columns:
        values = frame[column].map(_coerce_non_empty_text)
    else:
        values = pd.Series(
            [_required_text(fallback, target_name)] * len(frame),
            index=frame.index,
        )
    if values.isna().any():
        raise ValueError(f"{target_name} contains missing or empty values.")
    return values


def _numeric_values(series: pd.Series, *, column: str) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.isna().any():
        raise ValueError(f"{column} contains non-numeric or missing values.")
    return values.astype(float)


def _seconds_to_timedelta(value: float | None, name: str) -> timedelta | None:
    if value is None:
        return None
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero when provided.")
    return timedelta(seconds=value)


def _require_column(frame: pd.DataFrame, column: str) -> None:
    if column not in frame.columns:
        raise ValueError(f"Input frame is missing required column: {column}")


def _required_text(value: str | None, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} must be provided.")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} must be non-empty.")
    return text


def _coerce_non_empty_text(value: object) -> str | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


if __name__ == "__main__":
    main()

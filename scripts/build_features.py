"""Build offline microstructure feature frames from tick-like replay rows."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"
for import_path in (SCRIPT_ROOT, SRC_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from cli_utils import dataframe_records, load_frame, write_frame, write_json

from scalper_ai.domain import TickEvent
from scalper_ai.features.offline import build_feature_frame
from scalper_ai.features.schema import FeatureConfig, feature_names

TICK_TIMESTAMP_COLUMNS = ("event_timestamp", "received_timestamp")
REQUIRED_TICK_COLUMNS = ("event_timestamp", "received_timestamp", "bid", "ask")
METADATA_COLUMNS = (
    "symbol",
    "event_timestamp",
    "available_timestamp",
    "feature_set",
    "feature_version",
)


def build_features_cli(
    *,
    input_path: Path,
    output_path: Path,
    summary_output_path: Path | None = None,
    symbol: str | None = None,
    venue: str | None = None,
    volatility_window: int = 20,
    quote_intensity_window_seconds: float = 60.0,
    ofi_window: int = 10,
    toxicity_window: int = 20,
    mlofi_depth: int = 5,
    compression: str = "zstd",
) -> dict[str, object]:
    """Build an offline feature frame and return a JSON-ready summary payload."""

    tick_frame = load_frame(input_path, timestamp_columns=TICK_TIMESTAMP_COLUMNS)
    ticks = _ticks_from_frame(tick_frame, symbol=symbol, venue=venue)
    config = FeatureConfig(
        volatility_window=volatility_window,
        quote_intensity_window_seconds=quote_intensity_window_seconds,
        ofi_window=ofi_window,
        toxicity_window=toxicity_window,
        mlofi_depth=mlofi_depth,
    )

    feature_frame = build_feature_frame(ticks=ticks, config=config)
    write_frame(feature_frame, output_path, compression=compression)

    output_columns = list(feature_frame.columns)
    payload: dict[str, object] = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "input_rows": len(tick_frame),
        "feature_rows": len(feature_frame),
        "columns": output_columns,
        "feature_columns": [
            name for name in feature_names(mlofi_depth=config.mlofi_depth) if name in output_columns
        ],
        "metadata_columns": [name for name in METADATA_COLUMNS if name in output_columns],
        "config": {
            "volatility_window": config.volatility_window,
            "quote_intensity_window_seconds": config.quote_intensity_window_seconds,
            "ofi_window": config.ofi_window,
            "toxicity_window": config.toxicity_window,
            "mlofi_depth": config.mlofi_depth,
        },
        "preview": dataframe_records(feature_frame.head(3)),
    }
    write_json(payload, summary_output_path)
    return payload


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Build offline microstructure feature rows from tick-like replay rows.",
    )
    parser.add_argument("--input-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument(
        "--summary-output-path",
        type=Path,
        default=None,
        help="Optional JSON summary destination.",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Fallback symbol when the input frame has no symbol column.",
    )
    parser.add_argument(
        "--venue",
        default=None,
        help="Fallback venue when the input frame has no venue column.",
    )
    parser.add_argument("--volatility-window", type=int, default=20)
    parser.add_argument("--quote-intensity-window-seconds", type=float, default=60.0)
    parser.add_argument("--ofi-window", type=int, default=10)
    parser.add_argument("--toxicity-window", type=int, default=20)
    parser.add_argument("--mlofi-depth", type=int, default=5)
    parser.add_argument(
        "--compression",
        default="zstd",
        help="Parquet compression codec, or 'none'. Ignored for CSV output.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    payload = build_features_cli(
        input_path=args.input_path,
        output_path=args.output_path,
        summary_output_path=args.summary_output_path,
        symbol=args.symbol,
        venue=args.venue,
        volatility_window=args.volatility_window,
        quote_intensity_window_seconds=args.quote_intensity_window_seconds,
        ofi_window=args.ofi_window,
        toxicity_window=args.toxicity_window,
        mlofi_depth=args.mlofi_depth,
        compression=args.compression,
    )
    print(write_json(payload, None))


def _ticks_from_frame(
    frame: pd.DataFrame,
    *,
    symbol: str | None,
    venue: str | None,
) -> list[TickEvent]:
    _validate_tick_columns(frame, symbol=symbol, venue=venue)

    ticks: list[TickEvent] = []
    for row in frame.to_dict(orient="records"):
        ticks.append(
            TickEvent(
                symbol=_required_string(row, column="symbol", fallback=symbol),
                venue=_required_string(row, column="venue", fallback=venue),
                event_timestamp=_required_timestamp(row, "event_timestamp"),
                received_timestamp=_required_timestamp(row, "received_timestamp"),
                bid=float(row["bid"]),
                ask=float(row["ask"]),
                bid_size=_optional_float(row, "bid_size"),
                ask_size=_optional_float(row, "ask_size"),
                last_price=_optional_float(row, "last_price"),
                last_size=_optional_float(row, "last_size"),
                sequence=_optional_int(row, "sequence"),
            )
        )
    _validate_single_market_stream(ticks)
    return ticks


def _validate_tick_columns(
    frame: pd.DataFrame,
    *,
    symbol: str | None,
    venue: str | None,
) -> None:
    missing = [column for column in REQUIRED_TICK_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Input frame is missing required tick columns: {', '.join(missing)}")
    if "symbol" not in frame.columns and symbol is None:
        raise ValueError("Input frame must include a symbol column or --symbol must be provided.")
    if "venue" not in frame.columns and venue is None:
        raise ValueError("Input frame must include a venue column or --venue must be provided.")


def _required_string(
    row: dict[str, object],
    *,
    column: str,
    fallback: str | None,
) -> str:
    value = row.get(column)
    if _is_missing(value):
        if fallback is None:
            raise ValueError(f"Missing required {column} value.")
        return fallback
    return str(value)


def _required_timestamp(row: dict[str, object], column: str) -> datetime:
    value = row[column]
    if _is_missing(value):
        raise ValueError(f"Missing required {column} value.")
    return pd.Timestamp(value).to_pydatetime()


def _optional_float(row: dict[str, object], column: str) -> float | None:
    value = row.get(column)
    if _is_missing(value):
        return None
    return float(value)


def _optional_int(row: dict[str, object], column: str) -> int | None:
    value = row.get(column)
    if _is_missing(value):
        return None
    return int(value)


def _validate_single_market_stream(ticks: list[TickEvent]) -> None:
    streams = {(tick.symbol, tick.venue) for tick in ticks}
    if len(streams) > 1:
        raise ValueError(
            "Offline feature building expects one symbol/venue stream per run. "
            "Run the CLI separately for each market stream."
        )


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    missing = pd.isna(value)
    if hasattr(missing, "item"):
        return bool(missing.item())
    return bool(missing)


if __name__ == "__main__":
    main()

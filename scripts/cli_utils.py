"""Shared helpers for production-facing CLI entrypoints."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TypeAlias

import pandas as pd

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]

DEFAULT_TIMESTAMP_COLUMNS = (
    "event_timestamp",
    "available_timestamp",
    "target_end_timestamp",
    "target_end_event_timestamp",
)


def load_frame(
    path: Path,
    *,
    timestamp_columns: Sequence[str] = DEFAULT_TIMESTAMP_COLUMNS,
) -> pd.DataFrame:
    """Load CSV or Parquet into a dataframe and preserve UTC-aware timestamp columns."""

    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path)
    elif suffix in {".parquet", ".pq"}:
        frame = pd.read_parquet(path)
    else:
        raise ValueError(f"Unsupported frame input format: {path.suffix}")

    return normalize_timestamp_columns(frame, timestamp_columns=timestamp_columns)


def normalize_timestamp_columns(
    frame: pd.DataFrame,
    *,
    timestamp_columns: Sequence[str] = DEFAULT_TIMESTAMP_COLUMNS,
) -> pd.DataFrame:
    """Normalize present timestamp columns to pandas UTC timezone dtype."""

    normalized = frame.copy()
    for column in timestamp_columns:
        if column not in normalized.columns:
            continue
        normalized[column] = _normalize_utc_timestamp_column(normalized[column], column_name=column)
    return normalized


def write_frame(frame: pd.DataFrame, path: Path, *, compression: str = "zstd") -> Path:
    """Write a dataframe to CSV or Parquet based on the destination suffix."""

    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame.to_csv(path, index=False)
        return path
    if suffix in {".parquet", ".pq"}:
        frame.to_parquet(
            path,
            index=False,
            compression=None if compression == "none" else compression,
        )
        return path
    raise ValueError(f"Unsupported frame output format: {path.suffix}")


def dataframe_records(frame: pd.DataFrame) -> list[JsonObject]:
    """Return JSON-ready records from a dataframe."""

    return [json_ready_dict(record) for record in frame.to_dict(orient="records")]


def write_json(payload: Mapping[str, object], path: Path | None) -> str:
    """Serialize JSON payload and optionally persist it."""

    json_payload = json_ready_dict(payload)
    rendered = json.dumps(json_payload, indent=2, sort_keys=True)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{rendered}\n", encoding="utf-8")
    return rendered


def json_ready_dict(payload: Mapping[str, object]) -> JsonObject:
    """Return a mapping converted to JSON-safe primitives."""

    return {str(key): json_ready_value(value) for key, value in payload.items()}


def json_ready_value(value: object) -> JsonValue:
    """Convert common dataframe/numeric values into JSON-safe primitives."""

    if value is None:
        return None
    if isinstance(value, str | bool):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        if value.tzinfo is not None and value.utcoffset() is not None:
            return str(value.tz_convert("UTC").isoformat()).replace("+00:00", "Z")
        return str(value.isoformat())
    if isinstance(value, pd.Timedelta):
        return float(value.total_seconds())
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if hasattr(value, "item"):
        return json_ready_value(value.item())
    if isinstance(value, Mapping):
        return json_ready_dict(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [json_ready_value(item) for item in value]
    return str(value)


def _normalize_utc_timestamp_column(series: pd.Series, *, column_name: str) -> pd.Series:
    normalized_values: list[pd.Timestamp] = []
    for value in series:
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            raise ValueError(f"{column_name} contains invalid timestamps.")
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"{column_name} must contain UTC-aware timestamps.")
        normalized_values.append(timestamp.tz_convert("UTC"))
    return pd.Series(
        normalized_values,
        index=series.index,
        name=series.name,
        dtype="datetime64[ns, UTC]",
    )

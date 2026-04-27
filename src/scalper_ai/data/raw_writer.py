"""Parquet persistence for raw canonical market data."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from scalper_ai.domain import DomainModel

EventT = TypeVar("EventT", bound=DomainModel)

TIMESTAMP_FIELD_PRIORITY = (
    "event_timestamp",
    "available_timestamp",
    "created_at",
    "timestamp",
    "received_timestamp",
)


class RawParquetWriter:
    """Persist raw events to partitioned Parquet datasets."""

    def __init__(self, root_dir: Path, dataset_name: str, compression: str = "zstd") -> None:
        normalized_dataset = dataset_name.strip()
        if not normalized_dataset:
            raise ValueError("dataset_name must be non-empty.")
        self._root_dir = root_dir
        self._dataset_name = normalized_dataset
        self._compression = None if compression == "none" else compression

    def write_batch(self, events: Sequence[EventT]) -> list[Path]:
        """Persist a batch of events and return created Parquet files."""

        if not events:
            return []

        grouped_records: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            symbol = self._extract_symbol(event)
            event_date = self._extract_event_date(event)
            record = event.to_record(exclude_none=False)
            # `symbol` is already encoded in the dataset partition path. Keeping it inside the
            # file payload causes schema collisions when Arrow reconstructs partition columns.
            record.pop("symbol", None)
            record["dataset"] = self._dataset_name
            record["event_type"] = type(event).__name__
            record["ingested_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            grouped_records[(symbol, event_date)].append(record)

        output_paths: list[Path] = []
        for (symbol, event_date), records in grouped_records.items():
            partition_dir = self._root_dir / self._dataset_name / f"symbol={symbol}" / f"event_date={event_date}"
            partition_dir.mkdir(parents=True, exist_ok=True)
            output_path = partition_dir / f"{self._dataset_name}-{uuid4().hex}.parquet"
            table = pa.Table.from_pylist(records)
            pq.write_table(table, output_path, compression=self._compression)
            output_paths.append(output_path)

        return sorted(output_paths)

    @staticmethod
    def _extract_symbol(event: DomainModel) -> str:
        symbol = getattr(event, "symbol", "unknown")
        return str(symbol)

    @staticmethod
    def _extract_event_date(event: DomainModel) -> str:
        for field_name in TIMESTAMP_FIELD_PRIORITY:
            timestamp = getattr(event, field_name, None)
            if isinstance(timestamp, datetime):
                return timestamp.astimezone(timezone.utc).date().isoformat()
        raise ValueError(f"Unable to determine event date for {type(event).__name__}.")

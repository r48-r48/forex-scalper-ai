"""Replay-based ingestion sources for ticks and order book snapshots."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Generic, TypeVar

import pyarrow.parquet as pq
import orjson

from scalper_ai.domain import BookSnapshot, DomainModel, TickEvent

EventT = TypeVar("EventT", bound=DomainModel)

TIMESTAMP_FIELD_PRIORITY = (
    "event_timestamp",
    "available_timestamp",
    "created_at",
    "timestamp",
    "received_timestamp",
)


class ReplayEventSource(Generic[EventT]):
    """Read canonical domain events from JSONL or Parquet replay files."""

    def __init__(
        self,
        path: Path,
        model_type: type[EventT],
        symbols: Sequence[str] | None = None,
    ) -> None:
        self._path = path
        self._model_type = model_type
        self._symbols = set(symbols) if symbols is not None else None

    def stream(self, *, limit: int | None = None) -> Iterator[EventT]:
        """Yield events in deterministic timestamp order."""

        records = self._load_records()
        events = [self._model_type.from_record(record) for record in records]
        events.sort(key=self._event_sort_key)

        emitted = 0
        for event in events:
            if self._symbols is not None and getattr(event, "symbol", None) not in self._symbols:
                continue
            yield event
            emitted += 1
            if limit is not None and emitted >= limit:
                break

    def _load_records(self) -> list[dict[str, Any]]:
        suffix = self._path.suffix.lower()
        if suffix == ".jsonl":
            return self._load_jsonl_records()
        if suffix == ".parquet":
            return self._load_parquet_records()
        raise ValueError(f"Unsupported replay file format: {self._path.suffix}")

    def _load_jsonl_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with self._path.open("rb") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                payload = orjson.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("JSONL replay lines must contain JSON objects.")
                records.append(payload)
        return records

    def _load_parquet_records(self) -> list[dict[str, Any]]:
        table = pq.read_table(self._path)
        records = table.to_pylist()
        return [dict(record) for record in records]

    @staticmethod
    def _event_sort_key(event: EventT) -> tuple[str, str]:
        for field_name in TIMESTAMP_FIELD_PRIORITY:
            timestamp = getattr(event, field_name, None)
            if timestamp is not None:
                return (timestamp.isoformat(), event.to_json_str(exclude_none=True))
        return ("", event.to_json_str(exclude_none=True))


class ReplayTickSource(ReplayEventSource[TickEvent]):
    """Replay source for canonical tick events."""

    def __init__(self, path: Path, symbols: Sequence[str] | None = None) -> None:
        super().__init__(path=path, model_type=TickEvent, symbols=symbols)


class ReplayBookSource(ReplayEventSource[BookSnapshot]):
    """Replay source for canonical order book snapshots."""

    def __init__(self, path: Path, symbols: Sequence[str] | None = None) -> None:
        super().__init__(path=path, model_type=BookSnapshot, symbols=symbols)

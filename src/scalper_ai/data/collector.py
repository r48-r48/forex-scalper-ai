"""Replay-driven collection services for raw market data ingestion."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from scalper_ai.data.buffering import BufferedBatchWriter
from scalper_ai.domain import DomainModel

EventT = TypeVar("EventT", bound=DomainModel)
EventT_co = TypeVar("EventT_co", bound=DomainModel, covariant=True)


class ReplaySource(Protocol[EventT_co]):
    """Source contract for replay-driven ingestion."""

    def stream(self, *, limit: int | None = None) -> Iterator[EventT_co]:
        """Yield replay events up to an optional limit."""


@dataclass
class IngestionRunStats:
    """Summary of one ingestion run."""

    events_read: int = 0
    files_written: list[Path] = field(default_factory=list)

    @property
    def batches_written(self) -> int:
        """Return how many output files were created during the run."""

        return len(self.files_written)


class ReplayCollector(Generic[EventT]):
    """Collect replay events into a buffered raw writer."""

    def __init__(self, source: ReplaySource[EventT], writer: BufferedBatchWriter[EventT]) -> None:
        self._source = source
        self._writer = writer

    def run(self, *, limit: int | None = None) -> IngestionRunStats:
        """Consume replay events and persist them in buffered batches."""

        stats = IngestionRunStats()
        stream = self._source.stream
        for event in stream(limit=limit):
            stats.events_read += 1
            stats.files_written.extend(self._writer.write(event))
        stats.files_written.extend(self._writer.flush())
        return stats

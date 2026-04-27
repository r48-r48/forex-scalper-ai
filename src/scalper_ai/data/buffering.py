"""Batching helpers for event ingestion pipelines."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Generic, TypeVar

from scalper_ai.data.interfaces import BatchWriter
from scalper_ai.domain import DomainModel

EventT = TypeVar("EventT", bound=DomainModel)


class EventBatcher(Generic[EventT]):
    """Accumulate events until the configured batch size is reached."""

    def __init__(self, batch_size: int) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero.")
        self._batch_size = batch_size
        self._buffer: list[EventT] = []

    @property
    def size(self) -> int:
        """Return the number of currently buffered events."""

        return len(self._buffer)

    def add(self, event: EventT) -> list[EventT]:
        """Add one event and return a flushable batch if full."""

        self._buffer.append(event)
        if len(self._buffer) < self._batch_size:
            return []
        return self.flush()

    def extend(self, events: Iterable[EventT]) -> list[list[EventT]]:
        """Add many events and return all full batches emitted in order."""

        batches: list[list[EventT]] = []
        for event in events:
            batch = self.add(event)
            if batch:
                batches.append(batch)
        return batches

    def flush(self) -> list[EventT]:
        """Return buffered events and clear the buffer."""

        if not self._buffer:
            return []
        batch = list(self._buffer)
        self._buffer.clear()
        return batch


class BufferedBatchWriter(Generic[EventT]):
    """Buffer events before delegating full batches to a batch writer."""

    def __init__(self, writer: BatchWriter[EventT], batch_size: int) -> None:
        self._writer = writer
        self._batcher = EventBatcher[EventT](batch_size=batch_size)

    @property
    def buffered_size(self) -> int:
        """Expose current buffered event count for monitoring and tests."""

        return self._batcher.size

    def write(self, event: EventT) -> list[Path]:
        """Write one event, flushing to disk when a batch is full."""

        batch = self._batcher.add(event)
        if not batch:
            return []
        return self._writer.write_batch(batch)

    def write_many(self, events: Iterable[EventT]) -> list[Path]:
        """Write many events and return all paths produced across flushes."""

        paths: list[Path] = []
        for batch in self._batcher.extend(events):
            paths.extend(self._writer.write_batch(batch))
        return paths

    def flush(self) -> list[Path]:
        """Force persistence of any remaining buffered events."""

        batch = self._batcher.flush()
        if not batch:
            return []
        return self._writer.write_batch(batch)

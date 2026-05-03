"""Unit tests for ingestion buffering helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from scalper_ai.data.buffering import BufferedBatchWriter, EventBatcher
from scalper_ai.domain import TickEvent


class RecordingWriter:
    """Simple in-memory writer used to assert batch flush behavior."""

    def __init__(self) -> None:
        self.batches: list[list[TickEvent]] = []

    def write_batch(self, events: list[TickEvent]) -> list[Path]:
        self.batches.append(list(events))
        return [Path(f"/tmp/batch-{len(self.batches)}.parquet")]


def make_tick(index: int) -> TickEvent:
    return TickEvent(
        symbol="EURUSD",
        venue="REPLAY",
        event_timestamp=datetime(2026, 3, 26, 9, 0, index, tzinfo=UTC),
        received_timestamp=datetime(2026, 3, 26, 9, 0, index, tzinfo=UTC),
        bid=1.0812 + (index * 0.00001),
        ask=1.0813 + (index * 0.00001),
    )


def test_event_batcher_flushes_when_batch_size_reached() -> None:
    batcher = EventBatcher[TickEvent](batch_size=2)

    assert batcher.add(make_tick(0)) == []
    batch = batcher.add(make_tick(1))

    assert len(batch) == 2
    assert batcher.size == 0


def test_buffered_batch_writer_flushes_remaining_events() -> None:
    writer = RecordingWriter()
    buffered_writer = BufferedBatchWriter(writer=writer, batch_size=2)

    paths = buffered_writer.write(make_tick(0))
    assert paths == []

    paths = buffered_writer.write(make_tick(1))
    assert len(paths) == 1
    assert len(writer.batches) == 1

    buffered_writer.write(make_tick(2))
    remaining = buffered_writer.flush()

    assert len(remaining) == 1
    assert len(writer.batches) == 2

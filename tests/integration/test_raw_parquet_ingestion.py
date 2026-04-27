"""Integration tests for replay collection into raw Parquet storage."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq

from scalper_ai.data import BufferedBatchWriter, RawParquetWriter, ReplayCollector, ReplayTickSource


def test_replay_collection_writes_partitioned_parquet(tmp_path: Path) -> None:
    replay_path = tmp_path / "ticks.jsonl"
    replay_path.write_text(
        "\n".join(
            [
                '{"symbol":"EURUSD","venue":"REPLAY","event_timestamp":"2026-03-26T09:00:00Z","received_timestamp":"2026-03-26T09:00:00Z","bid":1.0812,"ask":1.0813}',
                '{"symbol":"EURUSD","venue":"REPLAY","event_timestamp":"2026-03-26T09:00:01Z","received_timestamp":"2026-03-26T09:00:01Z","bid":1.08121,"ask":1.08131}',
            ]
        ),
        encoding="utf-8",
    )

    writer = RawParquetWriter(root_dir=tmp_path / "raw", dataset_name="ticks", compression="none")
    buffered_writer = BufferedBatchWriter(writer=writer, batch_size=2)
    collector = ReplayCollector(source=ReplayTickSource(replay_path), writer=buffered_writer)

    stats = collector.run()

    assert stats.events_read == 2
    assert stats.batches_written == 1
    assert stats.files_written[0].exists()

    table = pq.read_table(stats.files_written[0])
    payload = table.to_pylist()
    assert len(payload) == 2
    assert payload[0]["dataset"] == "ticks"
    assert payload[0]["event_type"] == "TickEvent"

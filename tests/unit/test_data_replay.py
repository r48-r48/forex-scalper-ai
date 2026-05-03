"""Unit tests for replay ingestion sources and MT5 scaffolds."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scalper_ai.data.mt5 import Mt5BookIngestionAdapter, Mt5TickIngestionAdapter
from scalper_ai.data.replay import ReplayBookSource, ReplayTickSource
from scalper_ai.domain import BookSnapshot, TickEvent


class FakeMt5Client:
    """Small fake client for MT5 adapter tests."""

    BOOK_TYPE_BUY = 1
    BOOK_TYPE_SELL = 2

    def copy_ticks_range(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        flags: int,
    ) -> list[dict[str, float]]:
        return [
            {
                "time_msc": 1774515600000,
                "bid": 1.0812,
                "ask": 1.0813,
                "last": 1.08125,
                "volume_real": 0.7,
            }
        ]

    def market_book_get(self, symbol: str) -> list[dict[str, float]]:
        return [
            {"type": self.BOOK_TYPE_BUY, "price": 1.0812, "volume_dbl": 1.1},
            {"type": self.BOOK_TYPE_SELL, "price": 1.0813, "volume_dbl": 1.3},
        ]


def test_replay_tick_source_reads_jsonl(tmp_path: Path) -> None:
    replay_path = tmp_path / "ticks.jsonl"
    replay_path.write_text(
        "\n".join(
            [
                '{"symbol":"EURUSD","venue":"REPLAY","event_timestamp":"2026-03-26T09:00:01Z","received_timestamp":"2026-03-26T09:00:01Z","bid":1.0812,"ask":1.0813}',
                '{"symbol":"EURUSD","venue":"REPLAY","event_timestamp":"2026-03-26T09:00:00Z","received_timestamp":"2026-03-26T09:00:00Z","bid":1.0811,"ask":1.0812}',
            ]
        ),
        encoding="utf-8",
    )

    events = list(ReplayTickSource(replay_path).stream())

    assert len(events) == 2
    assert isinstance(events[0], TickEvent)
    assert events[0].event_timestamp <= events[1].event_timestamp


def test_replay_book_source_reads_parquet(tmp_path: Path) -> None:
    replay_path = tmp_path / "books.parquet"
    table = pa.Table.from_pylist(
        [
            {
                "symbol": "EURUSD",
                "venue": "REPLAY",
                "event_timestamp": "2026-03-26T09:00:00Z",
                "received_timestamp": "2026-03-26T09:00:00Z",
                "bids": [{"side": "bid", "level": 1, "price": 1.0812, "size": 1.0}],
                "asks": [{"side": "ask", "level": 1, "price": 1.0813, "size": 1.1}],
                "is_full_snapshot": True,
            }
        ]
    )
    pq.write_table(table, replay_path)

    events = list(ReplayBookSource(replay_path).stream())

    assert len(events) == 1
    assert isinstance(events[0], BookSnapshot)


def test_mt5_tick_adapter_normalizes_payload() -> None:
    adapter = Mt5TickIngestionAdapter(client=FakeMt5Client(), symbol="EURUSD")

    events = list(
        adapter.stream(
            start_time=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            end_time=datetime(2026, 3, 26, 10, 0, tzinfo=UTC),
        )
    )

    assert len(events) == 1
    assert events[0].symbol == "EURUSD"
    assert events[0].source.value == "live"


def test_mt5_book_adapter_normalizes_snapshot() -> None:
    adapter = Mt5BookIngestionAdapter(client=FakeMt5Client(), symbol="EURUSD")

    snapshots = list(adapter.stream(limit=1))

    assert len(snapshots) == 1
    assert snapshots[0].bids[0].price == 1.0812
    assert snapshots[0].asks[0].price == 1.0813

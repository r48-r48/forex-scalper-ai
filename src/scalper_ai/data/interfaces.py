"""Protocols for market data ingestion sources and sinks."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol, TypeVar

from scalper_ai.domain import BookSnapshot, DomainModel, TickEvent

EventT = TypeVar("EventT", bound=DomainModel)


class TickStreamSource(Protocol):
    """Source of canonical tick events."""

    def stream(self, *, limit: int | None = None) -> Iterator[TickEvent]:
        """Yield canonical tick events."""


class BookStreamSource(Protocol):
    """Source of canonical book snapshots."""

    def stream(self, *, limit: int | None = None) -> Iterator[BookSnapshot]:
        """Yield canonical order book snapshots."""


class BatchWriter(Protocol[EventT]):
    """Writer contract for persisted event batches."""

    def write_batch(self, events: Sequence[EventT]) -> list[Path]:
        """Persist a batch of events and return created files."""

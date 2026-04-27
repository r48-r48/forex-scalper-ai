"""Offline feature building helpers for replay and dataset pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import pandas as pd

from scalper_ai.domain import BookSnapshot, FeatureSnapshot, TickEvent
from scalper_ai.features.macro import MacroContextProvider
from scalper_ai.features.online import OnlineFeatureCalculator
from scalper_ai.features.primitives import TopOfBookEvent
from scalper_ai.features.schema import FeatureConfig


@dataclass(frozen=True)
class OrderedFeatureEvent:
    """Sortable wrapper for merging offline market-event streams."""

    event: TopOfBookEvent
    priority: int

    @property
    def sort_key(self) -> tuple[object, object, int]:
        return (
            self.event.received_timestamp,
            self.event.event_timestamp,
            self.priority,
        )


def merge_feature_events(
    *,
    ticks: Sequence[TickEvent] = (),
    books: Sequence[BookSnapshot] = (),
) -> list[TopOfBookEvent]:
    """Merge tick and book streams in availability order without look-ahead."""

    ordered_events = [OrderedFeatureEvent(event=tick, priority=1) for tick in ticks]
    ordered_events.extend(OrderedFeatureEvent(event=book, priority=0) for book in books)
    ordered_events.sort(key=lambda item: item.sort_key)
    return [item.event for item in ordered_events]


def build_feature_snapshots(
    *,
    ticks: Sequence[TickEvent] = (),
    books: Sequence[BookSnapshot] = (),
    events: Optional[Iterable[TopOfBookEvent]] = None,
    config: Optional[FeatureConfig] = None,
    macro_provider: Optional[MacroContextProvider] = None,
) -> list[FeatureSnapshot]:
    """Build canonical feature snapshots from offline replay streams."""

    calculator = OnlineFeatureCalculator(config=config, macro_provider=macro_provider)
    ordered_events = list(events) if events is not None else merge_feature_events(ticks=ticks, books=books)
    return [calculator.update(event) for event in ordered_events]


def build_feature_frame(
    *,
    ticks: Sequence[TickEvent] = (),
    books: Sequence[BookSnapshot] = (),
    events: Optional[Iterable[TopOfBookEvent]] = None,
    config: Optional[FeatureConfig] = None,
    macro_provider: Optional[MacroContextProvider] = None,
) -> pd.DataFrame:
    """Build a flat pandas frame suitable for model input or persistence."""

    snapshots = build_feature_snapshots(
        ticks=ticks,
        books=books,
        events=events,
        config=config,
        macro_provider=macro_provider,
    )
    records: list[dict[str, object]] = []
    for snapshot in snapshots:
        record: dict[str, object] = {
            "symbol": snapshot.symbol,
            "event_timestamp": snapshot.event_timestamp,
            "available_timestamp": snapshot.available_timestamp,
            "feature_set": snapshot.feature_set,
            "feature_version": snapshot.feature_version,
        }
        record.update(snapshot.values)
        records.append(record)
    return pd.DataFrame.from_records(records)

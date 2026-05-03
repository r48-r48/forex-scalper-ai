"""MT5 ingestion scaffolds for ticks and market book snapshots."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from scalper_ai.domain import BookLevel, BookSide, BookSnapshot, EventSource, TickEvent


class Mt5ClientProtocol(Protocol):
    """Minimal MT5 client surface required by the ingestion adapters."""

    def copy_ticks_range(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        flags: int,
    ) -> Sequence[Any]:
        """Return a sequence of tick payloads."""

    def market_book_get(self, symbol: str) -> Sequence[Any] | None:
        """Return a snapshot of current book levels."""


class Mt5TickIngestionAdapter:
    """Normalize MT5 tick payloads into canonical TickEvent objects."""

    def __init__(self, client: Mt5ClientProtocol, symbol: str, venue: str = "MT5") -> None:
        self._client = client
        self._symbol = symbol
        self._venue = venue

    def stream(
        self,
        start_time: datetime,
        end_time: datetime,
        *,
        flags: int = 0,
        limit: int | None = None,
    ) -> Iterator[TickEvent]:
        """Yield canonical ticks from MT5 historical API responses."""

        emitted = 0
        for raw_tick in self._client.copy_ticks_range(self._symbol, start_time, end_time, flags):
            yield self._normalize_tick(raw_tick)
            emitted += 1
            if limit is not None and emitted >= limit:
                break

    def _normalize_tick(self, raw_tick: Any) -> TickEvent:
        payload = self._coerce_mapping(raw_tick)
        event_timestamp = self._extract_timestamp(payload, primary="time_msc", fallback="time")
        received_timestamp = self._extract_timestamp(
            payload,
            primary="time_msc",
            fallback="time",
        )
        return TickEvent(
            symbol=self._symbol,
            venue=self._venue,
            event_timestamp=event_timestamp,
            received_timestamp=received_timestamp,
            bid=float(payload["bid"]),
            ask=float(payload["ask"]),
            bid_size=self._optional_float(payload, "bid_volume"),
            ask_size=self._optional_float(payload, "ask_volume"),
            last_price=self._optional_float(payload, "last"),
            last_size=(
                self._optional_float(payload, "volume_real")
                or self._optional_float(payload, "volume")
            ),
            sequence=self._optional_int(payload, "flags"),
            source=EventSource.LIVE,
        )

    @staticmethod
    def _coerce_mapping(raw_payload: Any) -> Mapping[str, Any]:
        if isinstance(raw_payload, Mapping):
            return raw_payload
        if hasattr(raw_payload, "_asdict"):
            payload = raw_payload._asdict()
            if isinstance(payload, Mapping):
                return payload
        raise TypeError("Unsupported MT5 payload type; expected mapping-like object.")

    @staticmethod
    def _extract_timestamp(payload: Mapping[str, Any], primary: str, fallback: str) -> datetime:
        if primary in payload and payload[primary] is not None:
            return datetime.fromtimestamp(float(payload[primary]) / 1000.0, tz=UTC)
        if fallback in payload and payload[fallback] is not None:
            return datetime.fromtimestamp(float(payload[fallback]), tz=UTC)
        raise ValueError("MT5 payload does not contain a usable timestamp field.")

    @staticmethod
    def _optional_float(payload: Mapping[str, Any], key: str) -> float | None:
        value = payload.get(key)
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _optional_int(payload: Mapping[str, Any], key: str) -> int | None:
        value = payload.get(key)
        if value is None:
            return None
        return int(value)


class Mt5BookIngestionAdapter:
    """Normalize MT5 book snapshots into canonical BookSnapshot objects."""

    def __init__(
        self,
        client: Mt5ClientProtocol,
        symbol: str,
        venue: str = "MT5",
        max_depth: int | None = None,
    ) -> None:
        self._client = client
        self._symbol = symbol
        self._venue = venue
        self._max_depth = max_depth

    def stream(self, *, limit: int | None = 1) -> Iterator[BookSnapshot]:
        """Yield current market book snapshots by polling MT5."""

        emitted = 0
        while limit is None or emitted < limit:
            raw_book = self._client.market_book_get(self._symbol)
            if raw_book is None:
                break
            yield self._normalize_book(raw_book)
            emitted += 1

    def _normalize_book(self, raw_book: Sequence[Any]) -> BookSnapshot:
        snapshot_time = datetime.now(UTC)
        bids: list[BookLevel] = []
        asks: list[BookLevel] = []

        for entry in raw_book:
            payload = Mt5TickIngestionAdapter._coerce_mapping(entry)
            side = self._resolve_book_side(payload)
            target = bids if side == BookSide.BID else asks
            target.append(
                BookLevel(
                    side=side,
                    level=len(target) + 1,
                    price=float(payload["price"]),
                    size=float(payload.get("volume_dbl", payload.get("volume", 0.0))),
                    order_count=self._optional_int(payload, "orders"),
                )
            )

        if self._max_depth is not None:
            bids = bids[: self._max_depth]
            asks = asks[: self._max_depth]

        return BookSnapshot(
            symbol=self._symbol,
            venue=self._venue,
            event_timestamp=snapshot_time,
            received_timestamp=snapshot_time,
            bids=sorted(bids, key=lambda level: level.price, reverse=True),
            asks=sorted(asks, key=lambda level: level.price),
        )

    def _resolve_book_side(self, payload: Mapping[str, Any]) -> BookSide:
        raw_type = payload.get("type")
        bid_candidates = {
            getattr(self._client, "BOOK_TYPE_BUY", None),
            "bid",
            "buy",
            "BOOK_TYPE_BUY",
        }
        ask_candidates = {
            getattr(self._client, "BOOK_TYPE_SELL", None),
            "ask",
            "sell",
            "BOOK_TYPE_SELL",
        }
        if raw_type in bid_candidates:
            return BookSide.BID
        if raw_type in ask_candidates:
            return BookSide.ASK
        raise ValueError(f"Unsupported MT5 book side payload: {raw_type}")

    @staticmethod
    def _optional_int(payload: Mapping[str, Any], key: str) -> int | None:
        value = payload.get(key)
        if value is None:
            return None
        return int(value)

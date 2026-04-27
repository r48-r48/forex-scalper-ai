"""Replayable audit events for market, signal, execution, risk, and latency flows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import orjson

from scalper_ai.domain import DomainModel
from scalper_ai.domain.validators import (
    NonEmptyStr,
    UtcDatetime,
    ensure_utc_datetime,
    serialize_utc_datetime,
)

TIMESTAMP_FIELD_PRIORITY = (
    "event_timestamp",
    "available_timestamp",
    "created_at",
    "timestamp",
    "submitted_at",
    "updated_at",
    "recorded_at",
    "received_timestamp",
)

SYMBOL_FIELD_PRIORITY = (
    "symbol",
    "broker_symbol",
)


class JournalEventType(str, Enum):
    """Canonical categories for the unified audit journal."""

    MARKET_DATA = "market_data_event"
    SIGNAL = "signal_event"
    ORDER_REQUEST = "order_request_event"
    ORDER_RESPONSE = "order_response_event"
    FILL = "fill_event"
    POSITION_SNAPSHOT = "position_snapshot"
    RISK = "risk_event"
    LATENCY = "latency_event"


class JournalEvent(DomainModel):
    """One immutable audit journal record.

    The journal keeps the envelope strongly typed while letting payloads reuse existing domain
    records such as ticks, features, order intents, fills, positions, and execution states.
    """

    event_id: NonEmptyStr
    event_type: JournalEventType
    event_timestamp: UtcDatetime
    recorded_at: UtcDatetime
    source: NonEmptyStr
    payload_type: NonEmptyStr
    payload: dict[str, Any]
    schema_version: NonEmptyStr = "journal.v1"
    correlation_id: Optional[NonEmptyStr] = None
    causation_id: Optional[NonEmptyStr] = None
    strategy_id: Optional[NonEmptyStr] = None
    symbol: Optional[NonEmptyStr] = None

    @classmethod
    def from_payload(
        cls,
        *,
        event_id: str,
        event_type: JournalEventType | str,
        payload: DomainModel | Mapping[str, Any],
        recorded_at: datetime,
        source: str,
        event_timestamp: datetime | None = None,
        payload_type: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        strategy_id: str | None = None,
        symbol: str | None = None,
    ) -> "JournalEvent":
        """Create one journal event from an existing domain record or JSON-ready mapping."""

        normalized_payload = normalize_journal_payload(payload)
        inferred_payload_type = payload_type or _infer_payload_type(payload)
        inferred_timestamp = (
            event_timestamp or _extract_timestamp(payload, normalized_payload) or recorded_at
        )
        inferred_symbol = symbol or _extract_symbol(normalized_payload)
        return cls(
            event_id=event_id,
            event_type=event_type,
            event_timestamp=inferred_timestamp,
            recorded_at=recorded_at,
            source=source,
            payload_type=inferred_payload_type,
            payload=normalized_payload,
            correlation_id=correlation_id,
            causation_id=causation_id,
            strategy_id=strategy_id,
            symbol=inferred_symbol,
        )

    def to_flat_record(self) -> dict[str, Any]:
        """Return a Parquet-friendly flat record with payload encoded as sorted JSON text."""

        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "event_timestamp": serialize_utc_datetime(self.event_timestamp),
            "recorded_at": serialize_utc_datetime(self.recorded_at),
            "source": self.source,
            "payload_type": self.payload_type,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "payload_json": orjson.dumps(self.payload, option=orjson.OPT_SORT_KEYS).decode("utf-8"),
        }


def normalize_journal_payload(payload: DomainModel | Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one journal payload into stable JSON-compatible values."""

    if isinstance(payload, DomainModel):
        return payload.to_record(exclude_none=False)
    if isinstance(payload, Mapping):
        return _normalize_mapping(payload)
    raise TypeError("journal payload must be a DomainModel or mapping.")


def journal_events_to_flat_records(events: Sequence[JournalEvent]) -> list[dict[str, Any]]:
    """Convert journal events into flat records suitable for Parquet table construction."""

    return [event.to_flat_record() for event in events]


def _normalize_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _normalize_value(value) for key, value in payload.items()}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, DomainModel):
        return value.to_record(exclude_none=False)
    if isinstance(value, datetime):
        return serialize_utc_datetime(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return _normalize_mapping(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    return value


def _infer_payload_type(payload: DomainModel | Mapping[str, Any]) -> str:
    if isinstance(payload, DomainModel):
        return type(payload).__name__
    raw_type = payload.get("payload_type") if isinstance(payload, Mapping) else None
    if raw_type is not None and str(raw_type).strip():
        return str(raw_type).strip()
    return "mapping"


def _extract_timestamp(
    payload: DomainModel | Mapping[str, Any],
    normalized_payload: Mapping[str, Any],
) -> datetime | None:
    if isinstance(payload, DomainModel):
        for field_name in TIMESTAMP_FIELD_PRIORITY:
            timestamp = getattr(payload, field_name, None)
            coerced = _coerce_timestamp(timestamp)
            if coerced is not None:
                return coerced
    for field_name in TIMESTAMP_FIELD_PRIORITY:
        coerced = _coerce_timestamp(normalized_payload.get(field_name))
        if coerced is not None:
            return coerced
    return None


def _coerce_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc_datetime(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            return ensure_utc_datetime(datetime.fromisoformat(normalized))
        except ValueError:
            return None
    return None


def _extract_symbol(payload: Mapping[str, Any]) -> str | None:
    for field_name in SYMBOL_FIELD_PRIORITY:
        raw_symbol = payload.get(field_name)
        if raw_symbol is None:
            continue
        normalized = str(raw_symbol).strip()
        if normalized:
            return normalized
    return None

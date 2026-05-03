"""Pure offline data quality helpers for Forex tick replay datasets."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from numbers import Real
from typing import TypeAlias

from scalper_ai.domain import TickEvent

TickRecordInput: TypeAlias = TickEvent | Mapping[str, object]

_DEFAULT_DUPLICATE_ROW_FIELDS = (
    "symbol",
    "venue",
    "event_timestamp",
    "received_timestamp",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "last_price",
    "last_size",
    "sequence",
    "source",
)


class DataQualitySeverity(StrEnum):
    """Severity levels emitted by data quality checks."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True)
class DataQualityIssue:
    """One structured data quality issue suitable for logs or JSON artifacts."""

    code: str
    severity: DataQualitySeverity
    message: str
    row_index: int | None = None
    symbol: str | None = None
    field: str | None = None
    details: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("code must be non-empty.")
        if not self.message.strip():
            raise ValueError("message must be non-empty.")
        if self.row_index is not None and self.row_index < 0:
            raise ValueError("row_index must be non-negative when provided.")
        if self.symbol is not None and not self.symbol.strip():
            raise ValueError("symbol must be non-empty when provided.")
        if self.field is not None and not self.field.strip():
            raise ValueError("field must be non-empty when provided.")

    def to_record(self) -> dict[str, object]:
        """Return a JSON-friendly issue record."""

        payload: dict[str, object] = {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
        }
        if self.row_index is not None:
            payload["row_index"] = self.row_index
        if self.symbol is not None:
            payload["symbol"] = self.symbol
        if self.field is not None:
            payload["field"] = self.field
        if self.details is not None:
            payload["details"] = _json_ready(dict(self.details))
        return payload


@dataclass(frozen=True)
class TickDataQualityConfig:
    """Configuration for offline Forex tick/replay data quality checks."""

    event_timestamp_field: str = "event_timestamp"
    received_timestamp_field: str | None = "received_timestamp"
    symbol_field: str = "symbol"
    bid_field: str = "bid"
    ask_field: str = "ask"
    max_event_gap: timedelta | None = None
    max_received_event_lag: timedelta | None = None
    group_temporal_checks_by_symbol: bool = False
    duplicate_row_fields: tuple[str, ...] = _DEFAULT_DUPLICATE_ROW_FIELDS

    def __post_init__(self) -> None:
        required_fields = {
            "event_timestamp_field": self.event_timestamp_field,
            "symbol_field": self.symbol_field,
            "bid_field": self.bid_field,
            "ask_field": self.ask_field,
        }
        if self.received_timestamp_field is not None:
            required_fields["received_timestamp_field"] = self.received_timestamp_field
        for field_name, value in required_fields.items():
            if not value.strip():
                raise ValueError(f"{field_name} must be non-empty.")
        for threshold_name, threshold in {
            "max_event_gap": self.max_event_gap,
            "max_received_event_lag": self.max_received_event_lag,
        }.items():
            if threshold is not None and threshold <= timedelta(0):
                raise ValueError(f"{threshold_name} must be positive when provided.")
        if not self.duplicate_row_fields:
            raise ValueError("duplicate_row_fields must be non-empty.")
        for field_name in self.duplicate_row_fields:
            if not field_name.strip():
                raise ValueError("duplicate_row_fields must contain non-empty field names.")


@dataclass(frozen=True)
class DataQualityReport:
    """Aggregated report for one offline tick data quality validation run."""

    dataset_name: str | None
    record_count: int
    issues: tuple[DataQualityIssue, ...]
    config: TickDataQualityConfig
    max_event_gap_seconds: float | None = None
    max_received_event_lag_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.dataset_name is not None and not self.dataset_name.strip():
            raise ValueError("dataset_name must be non-empty when provided.")
        if self.record_count < 0:
            raise ValueError("record_count must be non-negative.")

    @property
    def error_count(self) -> int:
        """Return the number of error-level issues."""

        return sum(issue.severity is DataQualitySeverity.ERROR for issue in self.issues)

    @property
    def warning_count(self) -> int:
        """Return the number of warning-level issues."""

        return sum(issue.severity is DataQualitySeverity.WARN for issue in self.issues)

    @property
    def info_count(self) -> int:
        """Return the number of info-level issues."""

        return sum(issue.severity is DataQualitySeverity.INFO for issue in self.issues)

    @property
    def passed(self) -> bool:
        """Return whether the data passed error-level quality checks."""

        return self.error_count == 0

    def to_record(self) -> dict[str, object]:
        """Return a JSON-friendly report record."""

        payload: dict[str, object] = {
            "dataset_name": self.dataset_name,
            "record_count": self.record_count,
            "passed": self.passed,
            "issue_count": len(self.issues),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "max_event_gap_seconds": self.max_event_gap_seconds,
            "max_received_event_lag_seconds": self.max_received_event_lag_seconds,
            "config": {
                "event_timestamp_field": self.config.event_timestamp_field,
                "received_timestamp_field": self.config.received_timestamp_field,
                "symbol_field": self.config.symbol_field,
                "bid_field": self.config.bid_field,
                "ask_field": self.config.ask_field,
                "max_event_gap_seconds": _timedelta_seconds(self.config.max_event_gap),
                "max_received_event_lag_seconds": _timedelta_seconds(
                    self.config.max_received_event_lag
                ),
                "group_temporal_checks_by_symbol": self.config.group_temporal_checks_by_symbol,
                "duplicate_row_fields": list(self.config.duplicate_row_fields),
            },
            "issues": [issue.to_record() for issue in self.issues],
        }
        return payload


@dataclass(frozen=True)
class _NormalizedTickRecord:
    row_index: int
    symbol: str | None
    event_timestamp: datetime | None
    received_timestamp: datetime | None
    bid: float | None
    ask: float | None
    duplicate_signature: tuple[object, ...]


@dataclass(frozen=True)
class _TickQualityContext:
    records: tuple[_NormalizedTickRecord, ...]
    timestamp_issues: tuple[DataQualityIssue, ...]
    price_issues: tuple[DataQualityIssue, ...]


def validate_tick_data(
    records: Sequence[TickRecordInput],
    *,
    config: TickDataQualityConfig | None = None,
    dataset_name: str | None = None,
) -> DataQualityReport:
    """Validate canonical or raw Forex tick replay records and return a structured report."""

    resolved_config = config or TickDataQualityConfig()
    context = _build_context(records, resolved_config)
    issues = (
        context.timestamp_issues
        + context.price_issues
        + _check_received_event_lag(context, resolved_config)
        + _check_temporal_ordering(context, resolved_config)
        + _check_duplicate_records(context)
    )
    return DataQualityReport(
        dataset_name=dataset_name,
        record_count=len(records),
        issues=issues,
        config=resolved_config,
        max_event_gap_seconds=_max_event_gap_seconds(context, resolved_config),
        max_received_event_lag_seconds=_max_received_event_lag_seconds(context),
    )


def check_tick_timestamp_quality(
    records: Sequence[TickRecordInput],
    *,
    config: TickDataQualityConfig | None = None,
) -> tuple[DataQualityIssue, ...]:
    """Return timestamp-awareness and received/event timestamp lag issues."""

    resolved_config = config or TickDataQualityConfig()
    context = _build_context(records, resolved_config)
    return context.timestamp_issues + _check_received_event_lag(context, resolved_config)


def check_tick_price_quality(
    records: Sequence[TickRecordInput],
    *,
    config: TickDataQualityConfig | None = None,
) -> tuple[DataQualityIssue, ...]:
    """Return bid/ask price and crossed-quote issues."""

    resolved_config = config or TickDataQualityConfig()
    return _build_context(records, resolved_config).price_issues


def check_tick_temporal_ordering(
    records: Sequence[TickRecordInput],
    *,
    config: TickDataQualityConfig | None = None,
) -> tuple[DataQualityIssue, ...]:
    """Return non-decreasing ordering and large event-gap issues."""

    resolved_config = config or TickDataQualityConfig()
    context = _build_context(records, resolved_config)
    return _check_temporal_ordering(context, resolved_config)


def check_tick_duplicates(
    records: Sequence[TickRecordInput],
    *,
    config: TickDataQualityConfig | None = None,
) -> tuple[DataQualityIssue, ...]:
    """Return duplicate event timestamp and exact duplicate row issues."""

    resolved_config = config or TickDataQualityConfig()
    context = _build_context(records, resolved_config)
    return _check_duplicate_records(context)


def _build_context(
    records: Sequence[TickRecordInput],
    config: TickDataQualityConfig,
) -> _TickQualityContext:
    normalized_records: list[_NormalizedTickRecord] = []
    timestamp_issues: list[DataQualityIssue] = []
    price_issues: list[DataQualityIssue] = []

    for row_index, record in enumerate(records):
        symbol = _optional_text(_field_value(record, config.symbol_field))
        event_timestamp, issue = _coerce_utc_timestamp(
            _field_value(record, config.event_timestamp_field),
            row_index=row_index,
            field=config.event_timestamp_field,
            symbol=symbol,
        )
        if issue is not None:
            timestamp_issues.append(issue)

        received_timestamp: datetime | None = None
        if config.received_timestamp_field is not None:
            received_timestamp, issue = _coerce_utc_timestamp(
                _field_value(record, config.received_timestamp_field),
                row_index=row_index,
                field=config.received_timestamp_field,
                symbol=symbol,
            )
            if issue is not None:
                timestamp_issues.append(issue)

        bid, issue = _coerce_positive_price(
            _field_value(record, config.bid_field),
            row_index=row_index,
            field=config.bid_field,
            symbol=symbol,
        )
        if issue is not None:
            price_issues.append(issue)

        ask, issue = _coerce_positive_price(
            _field_value(record, config.ask_field),
            row_index=row_index,
            field=config.ask_field,
            symbol=symbol,
        )
        if issue is not None:
            price_issues.append(issue)

        if bid is not None and ask is not None and ask < bid:
            price_issues.append(
                DataQualityIssue(
                    code="crossed_quote",
                    severity=DataQualitySeverity.ERROR,
                    message="Tick ask price is lower than bid price.",
                    row_index=row_index,
                    symbol=symbol,
                    details={"bid": bid, "ask": ask},
                )
            )

        normalized_records.append(
            _NormalizedTickRecord(
                row_index=row_index,
                symbol=symbol,
                event_timestamp=event_timestamp,
                received_timestamp=received_timestamp,
                bid=bid,
                ask=ask,
                duplicate_signature=_build_duplicate_signature(
                    record,
                    event_timestamp=event_timestamp,
                    received_timestamp=received_timestamp,
                    bid=bid,
                    ask=ask,
                    config=config,
                ),
            )
        )

    return _TickQualityContext(
        records=tuple(normalized_records),
        timestamp_issues=tuple(timestamp_issues),
        price_issues=tuple(price_issues),
    )


def _check_received_event_lag(
    context: _TickQualityContext,
    config: TickDataQualityConfig,
) -> tuple[DataQualityIssue, ...]:
    if config.received_timestamp_field is None:
        return ()

    issues: list[DataQualityIssue] = []
    for record in context.records:
        if record.event_timestamp is None or record.received_timestamp is None:
            continue
        lag = record.received_timestamp - record.event_timestamp
        if lag < timedelta(0):
            issues.append(
                DataQualityIssue(
                    code="received_before_event_timestamp",
                    severity=DataQualitySeverity.ERROR,
                    message="Tick received_timestamp is earlier than event_timestamp.",
                    row_index=record.row_index,
                    symbol=record.symbol,
                    field=config.received_timestamp_field,
                    details={
                        "event_timestamp": record.event_timestamp,
                        "received_timestamp": record.received_timestamp,
                        "lag_seconds": lag.total_seconds(),
                    },
                )
            )
        elif config.max_received_event_lag is not None and lag > config.max_received_event_lag:
            issues.append(
                DataQualityIssue(
                    code="stale_received_event_timestamp_gap",
                    severity=DataQualitySeverity.WARN,
                    message="Tick received_timestamp is stale relative to event_timestamp.",
                    row_index=record.row_index,
                    symbol=record.symbol,
                    field=config.received_timestamp_field,
                    details={
                        "event_timestamp": record.event_timestamp,
                        "received_timestamp": record.received_timestamp,
                        "lag_seconds": lag.total_seconds(),
                        "threshold_seconds": config.max_received_event_lag.total_seconds(),
                    },
                )
            )
    return tuple(issues)


def _check_temporal_ordering(
    context: _TickQualityContext,
    config: TickDataQualityConfig,
) -> tuple[DataQualityIssue, ...]:
    issues: list[DataQualityIssue] = []
    previous_by_scope: dict[str, _NormalizedTickRecord] = {}

    for record in context.records:
        if record.event_timestamp is None:
            continue
        scope = _temporal_scope(record, config)
        previous = previous_by_scope.get(scope)
        if previous is not None and previous.event_timestamp is not None:
            gap = record.event_timestamp - previous.event_timestamp
            if gap < timedelta(0):
                issues.append(
                    DataQualityIssue(
                        code="non_monotonic_event_timestamp",
                        severity=DataQualitySeverity.ERROR,
                        message="Tick event timestamps must be non-decreasing.",
                        row_index=record.row_index,
                        symbol=record.symbol,
                        field=config.event_timestamp_field,
                        details={
                            "previous_row_index": previous.row_index,
                            "previous_event_timestamp": previous.event_timestamp,
                            "event_timestamp": record.event_timestamp,
                            "gap_seconds": gap.total_seconds(),
                            "scope": scope,
                        },
                    )
                )
            elif config.max_event_gap is not None and gap > config.max_event_gap:
                issues.append(
                    DataQualityIssue(
                        code="large_event_timestamp_gap",
                        severity=DataQualitySeverity.WARN,
                        message="Tick event timestamp gap exceeds the configured threshold.",
                        row_index=record.row_index,
                        symbol=record.symbol,
                        field=config.event_timestamp_field,
                        details={
                            "previous_row_index": previous.row_index,
                            "previous_event_timestamp": previous.event_timestamp,
                            "event_timestamp": record.event_timestamp,
                            "gap_seconds": gap.total_seconds(),
                            "threshold_seconds": config.max_event_gap.total_seconds(),
                            "scope": scope,
                        },
                    )
                )
        previous_by_scope[scope] = record

    return tuple(issues)


def _check_duplicate_records(context: _TickQualityContext) -> tuple[DataQualityIssue, ...]:
    issues: list[DataQualityIssue] = []
    first_timestamp_row_by_scope: dict[tuple[str, datetime], int] = {}
    first_signature_row: dict[tuple[object, ...], int] = {}

    for record in context.records:
        if record.event_timestamp is not None:
            timestamp_key = (_duplicate_timestamp_scope(record), record.event_timestamp)
            first_row_index = first_timestamp_row_by_scope.get(timestamp_key)
            if first_row_index is None:
                first_timestamp_row_by_scope[timestamp_key] = record.row_index
            else:
                issues.append(
                    DataQualityIssue(
                        code="duplicate_event_timestamp",
                        severity=DataQualitySeverity.WARN,
                        message="Duplicate tick event_timestamp detected for the same symbol.",
                        row_index=record.row_index,
                        symbol=record.symbol,
                        field="event_timestamp",
                        details={
                            "first_row_index": first_row_index,
                            "event_timestamp": record.event_timestamp,
                        },
                    )
                )

        first_row_index = first_signature_row.get(record.duplicate_signature)
        if first_row_index is None:
            first_signature_row[record.duplicate_signature] = record.row_index
        else:
            issues.append(
                DataQualityIssue(
                    code="duplicate_tick_row",
                    severity=DataQualitySeverity.WARN,
                    message="Exact duplicate tick row detected.",
                    row_index=record.row_index,
                    symbol=record.symbol,
                    details={"first_row_index": first_row_index},
                )
            )

    return tuple(issues)


def _coerce_utc_timestamp(
    value: object,
    *,
    row_index: int,
    field: str,
    symbol: str | None,
) -> tuple[datetime | None, DataQualityIssue | None]:
    if value is None:
        return None, DataQualityIssue(
            code="missing_timestamp",
            severity=DataQualitySeverity.ERROR,
            message="Required timestamp field is missing.",
            row_index=row_index,
            symbol=symbol,
            field=field,
        )

    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw_value = value.strip()
        if not raw_value:
            return None, DataQualityIssue(
                code="missing_timestamp",
                severity=DataQualitySeverity.ERROR,
                message="Required timestamp field is empty.",
                row_index=row_index,
                symbol=symbol,
                field=field,
            )
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            return None, DataQualityIssue(
                code="timestamp_parse_error",
                severity=DataQualitySeverity.ERROR,
                message="Timestamp value is not ISO-8601 parseable.",
                row_index=row_index,
                symbol=symbol,
                field=field,
                details={"value": raw_value},
            )
    else:
        return None, DataQualityIssue(
            code="unsupported_timestamp_type",
            severity=DataQualitySeverity.ERROR,
            message="Timestamp value must be a datetime or ISO-8601 string.",
            row_index=row_index,
            symbol=symbol,
            field=field,
            details={"value_type": type(value).__name__},
        )

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, DataQualityIssue(
            code="timestamp_not_timezone_aware",
            severity=DataQualitySeverity.ERROR,
            message="Timestamp must be timezone-aware.",
            row_index=row_index,
            symbol=symbol,
            field=field,
            details={"value": parsed.isoformat()},
        )

    offset = parsed.utcoffset()
    normalized = parsed.astimezone(UTC)
    if offset != timedelta(0):
        return normalized, DataQualityIssue(
            code="timestamp_not_utc",
            severity=DataQualitySeverity.ERROR,
            message="Timestamp must be encoded with a UTC offset.",
            row_index=row_index,
            symbol=symbol,
            field=field,
            details={
                "value": parsed.isoformat(),
                "offset_seconds": None if offset is None else offset.total_seconds(),
                "normalized_timestamp": normalized,
            },
        )

    return normalized, None


def _coerce_positive_price(
    value: object,
    *,
    row_index: int,
    field: str,
    symbol: str | None,
) -> tuple[float | None, DataQualityIssue | None]:
    if value is None:
        return None, DataQualityIssue(
            code="missing_price",
            severity=DataQualitySeverity.ERROR,
            message="Required price field is missing.",
            row_index=row_index,
            symbol=symbol,
            field=field,
        )
    if isinstance(value, bool) or not isinstance(value, Real):
        return None, DataQualityIssue(
            code="invalid_price_type",
            severity=DataQualitySeverity.ERROR,
            message="Price value must be numeric.",
            row_index=row_index,
            symbol=symbol,
            field=field,
            details={"value": value, "value_type": type(value).__name__},
        )

    price = float(value)
    if not math.isfinite(price):
        return None, DataQualityIssue(
            code="invalid_price",
            severity=DataQualitySeverity.ERROR,
            message="Price value must be finite.",
            row_index=row_index,
            symbol=symbol,
            field=field,
            details={"value": price},
        )
    if price <= 0:
        return None, DataQualityIssue(
            code="non_positive_price",
            severity=DataQualitySeverity.ERROR,
            message="Price value must be greater than zero.",
            row_index=row_index,
            symbol=symbol,
            field=field,
            details={"value": price},
        )

    return price, None


def _build_duplicate_signature(
    record: TickRecordInput,
    *,
    event_timestamp: datetime | None,
    received_timestamp: datetime | None,
    bid: float | None,
    ask: float | None,
    config: TickDataQualityConfig,
) -> tuple[object, ...]:
    values: list[object] = []
    for field_name in config.duplicate_row_fields:
        value: object
        if field_name == config.event_timestamp_field and event_timestamp is not None:
            value = event_timestamp
        elif field_name == config.received_timestamp_field and received_timestamp is not None:
            value = received_timestamp
        elif field_name == config.bid_field and bid is not None:
            value = bid
        elif field_name == config.ask_field and ask is not None:
            value = ask
        else:
            value = _field_value(record, field_name)
        values.append(_signature_value(value))
    return tuple(values)


def _field_value(record: TickRecordInput, field: str) -> object:
    if isinstance(record, TickEvent):
        return getattr(record, field, None)
    return record.get(field)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _temporal_scope(
    record: _NormalizedTickRecord,
    config: TickDataQualityConfig,
) -> str:
    if not config.group_temporal_checks_by_symbol:
        return "__global__"
    return _duplicate_timestamp_scope(record)


def _duplicate_timestamp_scope(record: _NormalizedTickRecord) -> str:
    return record.symbol or "__missing_symbol__"


def _max_event_gap_seconds(
    context: _TickQualityContext,
    config: TickDataQualityConfig,
) -> float | None:
    previous_by_scope: dict[str, _NormalizedTickRecord] = {}
    max_gap: float | None = None

    for record in context.records:
        if record.event_timestamp is None:
            continue
        scope = _temporal_scope(record, config)
        previous = previous_by_scope.get(scope)
        if previous is not None and previous.event_timestamp is not None:
            gap_seconds = (record.event_timestamp - previous.event_timestamp).total_seconds()
            if gap_seconds >= 0:
                max_gap = gap_seconds if max_gap is None else max(max_gap, gap_seconds)
        previous_by_scope[scope] = record

    return max_gap


def _max_received_event_lag_seconds(context: _TickQualityContext) -> float | None:
    max_lag: float | None = None
    for record in context.records:
        if record.event_timestamp is None or record.received_timestamp is None:
            continue
        lag_seconds = (record.received_timestamp - record.event_timestamp).total_seconds()
        max_lag = lag_seconds if max_lag is None else max(max_lag, lag_seconds)
    return max_lag


def _timedelta_seconds(value: timedelta | None) -> float | None:
    return None if value is None else value.total_seconds()


def _signature_value(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, Mapping):
        return tuple((str(key), _signature_value(item)) for key, item in sorted(value.items()))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return tuple(_signature_value(item) for item in value)
    return value


def _json_ready(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_ready(item) for item in value]
    return value

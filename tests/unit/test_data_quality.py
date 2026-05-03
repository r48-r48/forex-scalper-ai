"""Unit tests for offline tick data quality helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import orjson

from scalper_ai.data.quality import (
    DataQualitySeverity,
    TickDataQualityConfig,
    check_tick_duplicates,
    check_tick_price_quality,
    check_tick_temporal_ordering,
    check_tick_timestamp_quality,
    validate_tick_data,
)
from scalper_ai.domain import TickEvent


def _tick(
    *,
    symbol: str = "EURUSD",
    event_timestamp: object | None = None,
    received_timestamp: object | None = None,
    bid: object = 1.0812,
    ask: object = 1.0813,
    sequence: int | None = None,
) -> dict[str, object]:
    event_ts = event_timestamp or datetime(2026, 3, 26, 9, 0, tzinfo=UTC)
    received_ts = received_timestamp or event_ts
    payload: dict[str, object] = {
        "symbol": symbol,
        "venue": "REPLAY",
        "event_timestamp": event_ts,
        "received_timestamp": received_ts,
        "bid": bid,
        "ask": ask,
    }
    if sequence is not None:
        payload["sequence"] = sequence
    return payload


def _codes(report_or_issues: object) -> set[str]:
    issues = (
        report_or_issues.issues
        if hasattr(report_or_issues, "issues")
        else report_or_issues
    )
    return {issue.code for issue in issues}


def test_validate_tick_data_accepts_good_raw_and_domain_records() -> None:
    first = _tick(sequence=1)
    second = TickEvent(
        symbol="EURUSD",
        venue="REPLAY",
        event_timestamp=datetime(2026, 3, 26, 9, 0, 1, tzinfo=UTC),
        received_timestamp=datetime(2026, 3, 26, 9, 0, 1, tzinfo=UTC),
        bid=1.08125,
        ask=1.08135,
        sequence=2,
    )

    report = validate_tick_data([first, second], dataset_name="eurusd-replay")

    assert report.passed
    assert report.error_count == 0
    assert report.warning_count == 0
    assert report.to_record()["dataset_name"] == "eurusd-replay"
    assert report.to_record()["issues"] == []
    orjson.dumps(report.to_record())


def test_timestamp_quality_flags_naive_timestamps() -> None:
    issues = check_tick_timestamp_quality(
        [
            _tick(
                event_timestamp=datetime(2026, 3, 26, 9, 0),
                received_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            )
        ]
    )

    assert _codes(issues) == {"timestamp_not_timezone_aware"}
    assert issues[0].severity is DataQualitySeverity.ERROR


def test_timestamp_quality_flags_non_utc_offsets() -> None:
    issues = check_tick_timestamp_quality(
        [
            _tick(
                event_timestamp="2026-03-26T12:00:00+03:00",
                received_timestamp="2026-03-26T09:00:00Z",
            )
        ]
    )

    assert _codes(issues) == {"timestamp_not_utc"}


def test_temporal_quality_flags_non_monotonic_event_order() -> None:
    report = validate_tick_data(
        [
            _tick(event_timestamp=datetime(2026, 3, 26, 9, 0, 1, tzinfo=UTC)),
            _tick(event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC)),
        ]
    )

    assert "non_monotonic_event_timestamp" in _codes(report)
    assert not report.passed


def test_temporal_quality_flags_large_event_gaps() -> None:
    config = TickDataQualityConfig(max_event_gap=timedelta(seconds=30))

    report = validate_tick_data(
        [
            _tick(event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC)),
            _tick(event_timestamp=datetime(2026, 3, 26, 9, 2, tzinfo=UTC)),
        ],
        config=config,
    )

    assert _codes(report) == {"large_event_timestamp_gap"}
    assert report.passed
    assert report.warning_count == 1
    assert report.max_event_gap_seconds == 120.0


def test_temporal_quality_can_group_ordering_by_symbol() -> None:
    records = [
        _tick(symbol="EURUSD", event_timestamp=datetime(2026, 3, 26, 9, 0, 1, tzinfo=UTC)),
        _tick(symbol="GBPUSD", event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC)),
    ]

    global_issues = check_tick_temporal_ordering(records)
    grouped_issues = check_tick_temporal_ordering(
        records,
        config=TickDataQualityConfig(group_temporal_checks_by_symbol=True),
    )

    assert _codes(global_issues) == {"non_monotonic_event_timestamp"}
    assert grouped_issues == ()


def test_duplicate_quality_flags_duplicate_event_timestamps() -> None:
    issues = check_tick_duplicates(
        [
            _tick(sequence=1),
            _tick(bid=1.08121, ask=1.08131, sequence=2),
        ]
    )

    assert _codes(issues) == {"duplicate_event_timestamp"}


def test_duplicate_quality_flags_exact_duplicate_rows() -> None:
    record = _tick(sequence=1)

    issues = check_tick_duplicates([record, dict(record)])

    assert {"duplicate_event_timestamp", "duplicate_tick_row"} == _codes(issues)


def test_price_quality_flags_missing_bid_or_ask() -> None:
    record = _tick()
    record.pop("ask")

    issues = check_tick_price_quality([record])

    assert _codes(issues) == {"missing_price"}
    assert issues[0].field == "ask"


def test_price_quality_flags_non_positive_prices() -> None:
    issues = check_tick_price_quality(
        [
            _tick(bid=0.0, ask=1.0813),
            _tick(bid=1.0812, ask=-1.0813),
        ]
    )

    assert _codes(issues) == {"non_positive_price"}
    assert len(issues) == 2


def test_price_quality_flags_invalid_price_types() -> None:
    issues = check_tick_price_quality([_tick(bid="bad", ask=1.0813)])

    assert _codes(issues) == {"invalid_price_type"}


def test_price_quality_flags_crossed_quotes() -> None:
    issues = check_tick_price_quality([_tick(bid=1.0814, ask=1.0813)])

    assert _codes(issues) == {"crossed_quote"}


def test_timestamp_quality_flags_stale_received_event_gap() -> None:
    config = TickDataQualityConfig(max_received_event_lag=timedelta(seconds=1))

    issues = check_tick_timestamp_quality(
        [
            _tick(
                event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
                received_timestamp=datetime(2026, 3, 26, 9, 0, 5, tzinfo=UTC),
            )
        ],
        config=config,
    )

    assert _codes(issues) == {"stale_received_event_timestamp_gap"}
    assert issues[0].severity is DataQualitySeverity.WARN


def test_timestamp_quality_flags_received_before_event() -> None:
    issues = check_tick_timestamp_quality(
        [
            _tick(
                event_timestamp=datetime(2026, 3, 26, 9, 0, 1, tzinfo=UTC),
                received_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
            )
        ]
    )

    assert _codes(issues) == {"received_before_event_timestamp"}
    assert issues[0].severity is DataQualitySeverity.ERROR

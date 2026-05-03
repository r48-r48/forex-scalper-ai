# Data Quality Pipeline

## Purpose

The data quality foundation validates Forex tick and replay records before they feed feature
engineering, labeling, supervised training, backtesting, or paper/live-safe runtime evidence.

These helpers are offline validation utilities only. They do not create trading decisions, do not
look into future labels, and do not change execution costs, spread, or slippage assumptions.

## API

Location: `src/scalper_ai/data/quality.py`

Primary entrypoint:

- `validate_tick_data(records, config=None, dataset_name=None)` returns a `DataQualityReport`.

Focused helper checks:

- `check_tick_timestamp_quality(records, config=None)`
- `check_tick_temporal_ordering(records, config=None)`
- `check_tick_duplicates(records, config=None)`
- `check_tick_price_quality(records, config=None)`

Input records may be canonical `TickEvent` instances or raw mapping records loaded from JSONL,
Parquet, or broker/vendor replay exports. Raw mapping support lets the QA step detect invalid rows
before Pydantic domain validation rejects them.

## Checks

Timestamp checks:

- required `event_timestamp`
- required `received_timestamp` when configured
- timestamp values must be timezone-aware
- timestamp values must be encoded with UTC offset
- `received_timestamp` must not be earlier than `event_timestamp`
- stale received/event lag is reported when `max_received_event_lag` is configured

Temporal replay checks:

- event timestamps must be non-decreasing in file/replay order
- large event gaps are reported when `max_event_gap` is configured
- temporal checks can optionally be grouped by symbol with
  `group_temporal_checks_by_symbol=True`

Duplicate checks:

- duplicate event timestamps are reported per symbol
- exact duplicate tick rows are reported from configurable signature fields

Quote checks:

- bid and ask must be present
- bid and ask must be numeric, finite, and greater than zero
- crossed quotes are reported when `ask < bid`

Zero-spread quotes, where `ask == bid`, are allowed by this foundation because the canonical
`TickEvent` contract allows them. Cost modeling remains explicit in backtesting/execution layers.

## Structured Reports

`DataQualityReport.to_record()` returns JSON-friendly primitives for logging or artifact storage:

```python
from datetime import UTC, datetime, timedelta

from scalper_ai.data import TickDataQualityConfig, validate_tick_data

records = [
    {
        "symbol": "EURUSD",
        "venue": "REPLAY",
        "event_timestamp": datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
        "received_timestamp": datetime(2026, 3, 26, 9, 0, tzinfo=UTC),
        "bid": 1.0812,
        "ask": 1.0813,
    }
]

report = validate_tick_data(
    records,
    dataset_name="eurusd-m1-replay",
    config=TickDataQualityConfig(
        max_event_gap=timedelta(seconds=5),
        max_received_event_lag=timedelta(seconds=1),
    ),
)

payload = report.to_record()
```

The report includes:

- `passed`
- `record_count`
- issue counts by severity
- observed max event gap and received/event lag
- the effective QA config
- a list of structured issues with code, severity, row index, symbol, field, and details

## Pipeline Placement

Recommended offline order:

1. Load raw replay rows from JSONL/Parquet or a vendor export.
2. Run `validate_tick_data()`.
3. Persist the report next to the dataset artifact.
4. Reject or quarantine datasets with error-level issues.
5. Materialize surviving rows into canonical `TickEvent` objects.
6. Continue into preprocessing, feature engineering, labels, and validation.

The QA layer intentionally stays separate from online trading logic. Live adapters and runtime
health providers may reuse the same report format for diagnostics, but the helpers do not submit,
cancel, repair, or size orders.

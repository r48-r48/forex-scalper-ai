# Event Journal Schema

## Purpose

The unified journal is the audit trail that ties market inputs, strategy decisions, broker
requests, broker responses, fills, positions, risk decisions, and latency measurements together.
It is intentionally an envelope around existing domain records instead of a parallel domain model.

## Event Envelope

Every journal record uses schema version `journal.v1`.

Required fields:

- `event_id` - unique event identifier from the caller.
- `event_type` - one of the canonical categories below.
- `event_timestamp` - UTC-aware event time for the underlying payload.
- `recorded_at` - UTC-aware time when the journal event was recorded.
- `source` - producing component, such as `replay`, `strategy`, `oms`, `execution`,
  `risk`, or `runtime`.
- `payload_type` - source payload type, for example `TickEvent`, `OrderIntent`,
  `FillEvent`, or `mapping`.
- `payload` - JSON-compatible payload. Existing domain models should be passed
  through `to_record()`.
- `schema_version` - currently `journal.v1`.

Optional correlation fields:

- `correlation_id` - stable id tying one decision chain together.
- `causation_id` - id of the direct upstream event.
- `strategy_id` - strategy or policy identifier when applicable.
- `symbol` - trading symbol inferred from the payload when available.

## Event Categories

- `market_data_event`
- `signal_event`
- `order_request_event`
- `order_response_event`
- `fill_event`
- `position_snapshot`
- `risk_event`
- `latency_event`

## Storage

JSONL audit files store one full `JournalEvent` JSON object per line.

Parquet-friendly export uses a flat record:

- envelope fields remain top-level scalar columns
- `payload_json` stores sorted JSON text for nested payload content

This keeps the first implementation simple while preserving a stable upgrade path to partitioned
Parquet datasets and richer query tables.

# Interface Map

This document records the current contracts and the target POST-PHASE contracts. It is intentionally descriptive first: the project already has useful protocols and domain contracts, so the next work should extend them rather than rewrite completed phases.

## Existing Contracts

### Domain Models

Location: `src/scalper_ai/domain`

Current canonical models:

- `TickEvent`
- `BookLevel`
- `BookSnapshot`
- `BarEvent`
- `FeatureSnapshot`
- `OrderIntent`
- `FillEvent`
- `PositionState`

Current rules:

- timestamps must be timezone-aware UTC
- internal position and fill quantities are base units, not broker lots
- models are immutable and serialization-ready
- feature snapshots use stable metadata plus flat `values`

### Market Data Adapter

Current locations:

- `src/scalper_ai/data/interfaces.py`
- `src/scalper_ai/data/replay.py`
- `src/scalper_ai/data/mt5.py`

Current contracts:

- `TickStreamSource`
- `BookStreamSource`
- `BatchWriter`
- replay JSONL/Parquet sources
- MT5 tick/book ingestion adapters

Target direction:

- keep adapters at the boundary
- normalize broker/vendor payloads into domain models
- connect journal writers without changing feature/model code

### Strategy

Current locations:

- `src/scalper_ai/backtesting/engine.py`
- `src/scalper_ai/rl/environment.py`
- `src/scalper_ai/models/transformer.py`

Current contracts:

- `TargetPositionStrategy` protocol for backtesting
- RL action policy helpers for offline environment
- supervised predictor wrapper for signal inference

Target direction:

- introduce a common strategy/policy surface after P0 journal and OMS contracts are in place
- support decisions such as do nothing, passive entry, aggressive entry, cancel, reduce, and quote skew
- keep baseline strategies separate from model internals

### Broker Adapter

Current locations:

- `src/scalper_ai/execution/interfaces.py`
- `src/scalper_ai/execution/paper.py`
- `src/scalper_ai/execution/live_stub.py`
- `src/scalper_ai/execution/mt5_live.py`
- `src/scalper_ai/execution/mt5_client.py`

Current contracts:

- `ExecutionAdapter`
- `BrokerSnapshotProvider`
- `BrokerConnectivityProvider`
- `ExecutionStateStore`
- `SqliteExecutionStateStore`
- paper execution adapter
- live stub adapter
- MT5 live adapter and terminal client wrapper
- broker-source-of-truth MT5 sizing refresh for target-position and reduce-only decisions
- hedging-aware MT5 position snapshots with position tickets, gross exposure, and source ticket ids

Target direction:

- MT5 live submission now runs `order_check` before `order_send`; full journal correlation and reconciliation flow continue through the journal/OMS work
- broker adapters should remain separated from domain logic and OMS/risk decisions
- runtime recovery should reload durable execution state before new orders and block unsafe live startup when recovered open orders cannot be reconciled
- deal-based accounting, protective order reconciliation, and symbol-specific quantization are the next broker-adapter hardening targets

### Journal

Current locations:

- `src/scalper_ai/journal/events.py`
- `src/scalper_ai/journal/writers.py`
- `src/scalper_ai/data/raw_writer.py`
- domain `to_record()` helpers
- execution/reconciliation snapshots

Current status:

- raw market persistence exists
- domain records serialize cleanly
- unified journal event envelope exists for market/signal/order/fill/position/risk/latency audit events
- JSONL journal writer and flat Parquet-friendly record export exist

Target contract:

- `market_data_event`
- `signal_event`
- `order_request_event`
- `order_response_event`
- `fill_event`
- `position_snapshot`
- `risk_event`
- `latency_event`

Schema doc:

- `docs/event-schema.md`

The journal must be usable in replay, paper, shadow, and live-safe paths.

### RiskEngine

Current locations:

- `src/scalper_ai/risk/engine.py`
- `src/scalper_ai/config/models.py`
- `src/scalper_ai/deployment/runtime.py`
- `src/scalper_ai/execution/reconciliation.py`

Current status:

- risk configuration exists
- standalone pre-trade RiskEngine contract exists
- live startup refuses unsafe kill-switch configuration
- reconciliation detects broker/internal drift

Target contract:

- approve/reject order intents before broker submission
- emit journalable risk events
- enforce max position, max daily loss, max order rate, duplicate detection, stale data kill, reject-burst kill, symbol kill, and session kill

### OMS

Current locations:

- `src/scalper_ai/services/oms.py`
- `src/scalper_ai/execution/models.py`
- `src/scalper_ai/execution/router.py`
- `src/scalper_ai/execution/paper.py`
- `src/scalper_ai/execution/mt5_live.py`

Current status:

- execution order lifecycle exists inside adapters
- standalone OMS lifecycle transition contract exists
- emergency flatten intent helper exists
- router separates paper and live paths

Target lifecycle:

```text
NEW -> CHECKED -> SENT -> ACK -> PARTIAL/FILLED/REJECTED/CANCELLED -> RECONCILED
```

The OMS should own idempotency, duplicate detection, correlation IDs, emergency flatten orchestration, and transition validation.

### PortfolioService

Current locations:

- `src/scalper_ai/backtesting/accounting.py`
- `src/scalper_ai/execution/paper.py`
- `src/scalper_ai/execution/mt5_live.py`

Current status:

- netting accounting exists
- paper and MT5 adapters reuse accounting math for position/equity updates
- no standalone portfolio service exists yet

Target direction:

- expose account/position/equity snapshots consistently across backtest, paper, and live-safe runtime
- keep broker lots conversion inside adapters
- keep base-unit portfolio accounting in domain/backtesting/execution core

## Immediate Interface Work

Do next:

1. Add deal-based live accounting and commission/fee/swap attribution.
2. Add protective TP/SL/bracket management and reconciliation.
3. Add symbol-specific MT5 capability discovery and conservative quantization.
4. Reuse existing domain, execution, deployment, journal, OMS/risk, and validation surfaces wherever possible.

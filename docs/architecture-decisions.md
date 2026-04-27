# Architecture Decisions

## ADR-001 — Canonical Internal Schemas Use Immutable Domain Models

- Status: Accepted
- Reason:
  - ingestion, replay, preprocessing, training, and execution need a stable shared contract
- Decision:
  - canonical project events live in `src/scalper_ai/domain`
  - they are immutable and serialization-ready
- Consequence:
  - adapter-specific payloads must be normalized at the boundaries

## ADR-002 — Internal Position And Fill Quantities Use Base Units

- Status: Accepted
- Reason:
  - broker lots are adapter-specific and reduce portability
- Decision:
  - internal order/fill/position size is represented in base units
- Consequence:
  - broker adapters must handle conversion to lots later

## ADR-003 — FeatureSnapshot Uses Hybrid Schema

- Status: Accepted
- Reason:
  - feature metadata must stay stable while allowing feature-set expansion
- Decision:
  - `FeatureSnapshot` keeps stable metadata fields plus `values: dict[str, float]`
- Consequence:
  - feature names must be stable and flat enough for storage and model consumption

## ADR-004 — Replay-First Fallback Is Mandatory

- Status: Accepted
- Reason:
  - broker terminals, credentials, or L2 feeds may be unavailable during development
- Decision:
  - every live-oriented path must be usable via replay/mock mode
- Consequence:
  - new phases should expose interfaces and replay-friendly implementations

## ADR-005 — Preprocessing Uses Streaming Builders

- Status: Accepted
- Reason:
  - future online pipelines need the same logic as offline research
- Decision:
  - bar builders are incremental and stateful, not batch-only dataframe transforms
- Consequence:
  - feature calculators should follow the same pattern where possible

## ADR-006 — Backtesting V1 Uses Target-Position Replay With Immediate Market Fills

- Status: Accepted
- Reason:
  - PHASE 9 needs deterministic evaluation that plugs directly into existing signal and RL research outputs
- Decision:
  - the first backtesting engine version replays a single symbol, netting-only account, target-position strategy interface, and immediate market execution with explicit spread/slippage/commission costs
- Consequence:
  - limit/stop order simulation, latency models, and multi-symbol portfolio logic stay out of scope until a later phase

## ADR-007 — Walk-Forward Validation Evaluates Test Folds Through The Backtester

- Status: Accepted
- Reason:
  - PHASE 10 needs out-of-sample evaluation that reuses existing split logic and explicit trading-cost accounting
- Decision:
  - validation orchestrates PHASE 6 walk-forward partitions, builds strategies per fold, and evaluates only the test partition through the PHASE 9 backtester while aggregating explicit fold metrics
- Consequence:
  - validation remains reporting-friendly and leakage-safe, while training/calibration details stay inside the strategy factory rather than inside the validation core

## ADR-008 — Execution V1 Uses A Paper-First Adapter Boundary

- Status: Accepted
- Reason:
  - PHASE 11 needs a safe execution architecture that can exercise order lifecycles before any live broker integration is trusted
- Decision:
  - execution exposes broker-agnostic models and adapter interfaces, ships a deterministic paper adapter first, and routes intents through an explicit paper/live boundary
- Consequence:
  - the system can validate routing, fills, and position updates end-to-end in paper mode while keeping later live adapters isolated behind protocols

## ADR-009 — Deployment V1 Boots Through A Paper-Safe Runtime Boundary

- Status: Accepted
- Reason:
  - PHASE 12 needs runtime orchestration and observability without silently promoting the system into unsafe live behavior
- Decision:
  - deployment bootstraps research, paper, and live-safe modes through one runtime wrapper, requires explicit live confirmation, and falls back to paper when live prerequisites are unavailable or unsafe
- Consequence:
  - operations can exercise health, metrics, and startup workflows immediately, while deeper live integrations stay behind explicit safety checks and adapter boundaries

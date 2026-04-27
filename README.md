# Forex Scalper AI

Production-oriented research and execution repository for a low-latency Forex scalping agent operating on tick and optional Level 2 / DOM data.

## Status

This repository was implemented in phased milestones. PHASE 12 completes the first deployment and operations layer on top of the PHASE 1-11 research, validation, execution, and paper-routing foundations.

## Design Principles

- Python 3.11+ with `src`-layout and explicit package boundaries.
- Config-driven startup with YAML overlays and environment variable overrides.
- Strict separation between domain models, data adapters, features, models, RL, execution, and risk controls.
- Paper mode by default, with hard risk controls outside the model path.
- UTC timestamps, explicit transaction costs, and reproducible experiments.

## Planned Phases

- [x] PHASE 1: Project bootstrap
- [x] PHASE 2: Market data domain model
- [x] PHASE 3: Data ingestion layer
- [x] PHASE 4: Bar builders and preprocessing
- [x] PHASE 5: Feature engineering
- [x] PHASE 6: Dataset builders
- [x] PHASE 7: Transformer signal model
- [x] PHASE 8: RL environment and policy training
- [x] PHASE 9: Backtesting engine
- [x] PHASE 10: Walk-forward and robustness validation
- [x] PHASE 11: Execution architecture
- [x] PHASE 12: Deployment and operations

## Repository Layout

```text
forex-scalper-ai/
  configs/
  data/
    raw/
    processed/
    artifacts/
  scripts/
  src/
    scalper_ai/
      backtesting/
      config/
      data/
      deployment/
      domain/
      execution/
      features/
      models/
      risk/
      rl/
      services/
      utils/
      validation/
  tests/
    unit/
    integration/
```

## Runtime Dependencies

Core runtime dependencies are declared in `pyproject.toml`. The base layer includes configuration, storage, and infra packages. Model training dependencies are isolated into optional extras to keep bootstrap lean.

## Quick Start

1. Create a Python 3.11+ virtual environment.
2. Install the package in editable mode with dev tools:

```bash
pip install -e ".[dev,ml]"
```

3. Copy the example environment file:

```bash
cp .env.example .env
```

4. Start Redis:

```bash
docker compose up -d redis
```

5. Run tests:

```bash
pytest
```

## Session Continuity

At the end of a work session, use the phrase `мы закончили` in chat. The active agent should then update the persistent handoff file:

- [AGENTS.md](/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/AGENTS.md)
- [AGENT_HANDOFF.md](/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/AGENT_HANDOFF.md)
- [SESSION_CHECKPOINT.md](/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/SESSION_CHECKPOINT.md)
- [current-state.md](/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/current-state.md)
- [architecture-decisions.md](/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/architecture-decisions.md)
- [todo-next.md](/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/todo-next.md)

To resume in a new window:

```bash
cd '/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai'
python3 scripts/handoff.py status
python3 scripts/handoff.py checkpoint
python3 scripts/handoff.py prompt
```

`scripts/handoff.py prompt` now prints a ready-to-paste instruction that points the next Codex session to `AGENTS.md`, handoff, current-state docs, and the persistent same-window checkpoint.

## Configuration

- Base settings live in `configs/base.yaml`.
- Environment overlays live in `configs/research.yaml`, `configs/paper.yaml`, and `configs/live.yaml`.
- Environment variable overrides use the `SCALPER_AI_` prefix.

Example:

```bash
export SCALPER_AI_ENV=research
export SCALPER_AI_LOG_LEVEL=DEBUG
```

## Current Bootstrap Components

- Typed application config via Pydantic and `pydantic-settings`.
- YAML config overlay loader with env overrides.
- UTC-aware JSON or console logging.
- Docker Compose service definition for Redis.
- Baseline unit tests for config and logging.

## Current Domain Layer

- Canonical immutable event models for ticks, book snapshots, features, order intents, fills, and position state.
- UTC-aware timestamp normalization with serialization helpers for replay/storage use cases.
- Shared enums and validation rules for market data integrity and broker-agnostic trading state.
- Unit and integration tests covering core schema invariants and round-trip serialization.

## Current Ingestion Layer

- Protocols for tick/book sources and batch writers.
- Replay JSONL/Parquet readers that emit canonical PHASE 2 models.
- MT5 tick and market book adapter scaffolds with normalization into internal schemas.
- Buffered batch writer and raw Parquet dataset writer for partitioned raw storage.
- Replay collector service and a `scripts/collect_ticks.py` entry point for replay-driven ingestion.

## Current Preprocessing Layer

- Canonical `BarEvent` schema for aggregated bars.
- Streaming builders for time bars, tick bars, volatility bars, and optional imbalance bars.
- Pure preprocessing helpers for mid-price extraction, volume proxies, and fractional differentiation.
- Unit tests covering bar aggregation logic and preprocessing math contracts.

## Current Feature Layer

- Stable feature schema and naming for flat `FeatureSnapshot` outputs.
- Pure primitives for spread, returns, realized volatility, and quote intensity.
- Order-flow helpers for OFI and graceful-degrading MLOFI.
- VPIN-like toxicity proxy plus placeholder macro context provider.
- Offline feature builders and an incremental online calculator with parity-oriented tests.

## Current Dataset Layer

- Leakage-safe target generation from feature frames using availability ordering.
- Supervised dataset builders that flatten trailing feature windows into model-ready rows.
- Walk-forward split helpers with embargo support for train/validation/test partitioning.
- Parquet export helper for downstream forecasting and RL training pipelines.

## Current Model Layer

- Baseline causal transformer encoder for supervised signal prediction.
- Explicit lagged-feature tensorizer that converts PHASE 6 dataset rows into sequence tensors.
- Thin predictor wrapper for batch and dataframe inference flows.
- Unit and integration tests covering tensor shapes, causal masking, and dataset-to-model compatibility.

## Current RL Layer

- Deterministic offline trading environment with explicit action, reward, and cost accounting.
- Baseline categorical trading policy network for discrete short/flat/long actions.
- Policy-gradient training helpers and episode rollout scaffolds.
- Unit and integration tests covering transitions, rewards, and multi-step episode behavior.

## Current Backtesting Layer

- Deterministic event-driven replay loop over historical feature or signal frames.
- Target-position strategy API that synthesizes explicit market `OrderIntent` and `FillEvent` records.
- Netting-only position accounting with marked cash, realized/unrealized PnL, equity, and drawdown tracking.
- Unit and integration tests covering fill cost decomposition, position transitions, and multi-step replay behavior.

## Current Validation Layer

- Walk-forward orchestration that reuses PHASE 6 dataset splits and PHASE 9 backtest execution.
- Fold-level metrics plus aggregate robustness summaries for pnl, drawdown, trade activity, and turnover.
- Helper that converts lagged supervised dataset partitions into current-state backtest frames using `lag_000__` features.
- Unit and integration tests covering aggregation, split-aware evaluation, and end-to-end out-of-sample replay.

## Current Execution Layer

- Broker-agnostic execution models and adapter protocol for routing `OrderIntent` without leaking broker details into core logic.
- Deterministic paper execution adapter with market, limit, stop, and stop-limit lifecycle handling.
- Explicit `FillEvent` and `PositionState` updates reusing the backtesting accounting math instead of duplicating fill logic.
- Router that separates paper and live adapter selection while keeping paper mode as the default safe path.
- Pure reconciliation helpers that compare internal order/position state against normalized broker snapshots before deeper live integration.
- Concrete live-facing stub adapter that implements the broker snapshot contract and plugs into runtime reconciliation end-to-end.
- MT5 live execution adapter plus a real terminal client wrapper with base-unit to lot conversion, normalized broker snapshots, and connectivity export.
- Broker connectivity snapshot contract plus runtime health and metrics checks for live-path dependency status and stale broker state.

## Current Deployment Layer

- Deployment runtime that bootstraps `research`, `paper`, and live-safe modes with explicit paper fallback when live conditions are not satisfied.
- Health-check and Prometheus-style metrics surfaces exposed through `src/scalper_ai/deployment`, including reconciliation and broker dependency checks.
- Runtime entrypoint script at `scripts/run_runtime.py` for summary, health, and metrics inspection.
- MT5-specific overlay at `configs/mt5.yaml` plus auto-discovery/preflight-aware smoke check at `scripts/mt5_smoke.py`.
- Config overlays and startup rules that keep paper mode as the default operational posture until explicit live-safe conditions are met.

## Architecture Risks Identified Early

- MT5 integration is platform-dependent and will require adapter isolation plus replay-mode fallback.
- Tick/L2 storage volume will grow quickly; Parquet partitioning and retention policies must be designed before live ingestion.
- Low-latency execution and ML inference will need careful offline/online feature parity to avoid research-production drift.

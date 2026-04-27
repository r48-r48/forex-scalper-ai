# Agent Handoff

## Project

- Repository: `forex-scalper-ai`
- Root path: `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai`
- Goal: production-grade AI Forex scalping agent with tick/M1/L2 support, Transformer forecasting, DRL policy layer, execution adapters, validation, and deployment.
- Persistent same-window checkpoint: `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/SESSION_CHECKPOINT.md`

## Completed Phases

### PHASE 1 — Project bootstrap

Implemented:
- `src`-layout repository
- `pyproject.toml`
- `.env.example`
- `docker-compose.yml` with Redis
- YAML config overlays
- typed config loader on Pydantic
- UTC-safe logging utilities
- baseline unit tests

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/pyproject.toml`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/configs/base.yaml`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/config/models.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/config/loader.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/utils/logging.py`

### PHASE 2 — Canonical market data domain model

Implemented:
- immutable domain base model
- shared validators
- canonical enums
- `TickEvent`
- `BookLevel`
- `BookSnapshot`
- `FeatureSnapshot`
- `OrderIntent`
- `FillEvent`
- `PositionState`

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/domain/base.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/domain/validators.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/domain/enums.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/domain/market.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/domain/features.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/domain/trading.py`

Important canonical decisions:
- all timestamps are UTC-aware
- internal size units are base units, not broker lots
- `FeatureSnapshot` uses hybrid schema: metadata + `values: dict[str, float]`

### PHASE 3 — Data ingestion layer

Implemented:
- source/writer protocols
- replay sources from JSONL/Parquet
- MT5 ingestion scaffolds
- event batching
- buffered writer
- raw Parquet persistence
- replay collector
- replay tick collection script

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/interfaces.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/replay.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/mt5.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/buffering.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/raw_writer.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/collector.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/scripts/collect_ticks.py`

### PHASE 4 — Bar builders and preprocessing

Implemented:
- canonical `BarEvent`
- `BarType`
- `TimeBarBuilder`
- `TickBarBuilder`
- `VolatilityBarBuilder`
- `ImbalanceBarBuilder`
- fractional differentiation
- preprocessing helpers for mid price / trade proxy / volume proxy

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/domain/bars.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/bar_builders.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/preprocessing.py`

### PHASE 5 — Feature engineering

Implemented:
- stable feature naming and config contracts
- pure spread / return / realized volatility primitives
- quote intensity logic
- OFI and graceful-degrading MLOFI
- VPIN-like toxicity proxy
- macro placeholder/provider interface
- incremental online feature calculator
- offline feature builder and flat feature frame export
- unit and integration tests for primitives and offline/online parity

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/__init__.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/schema.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/primitives.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/order_flow.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/toxicity.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/macro.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/offline.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/online.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_features_primitives.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_features_online.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/integration/test_feature_offline_online_parity.py`

### PHASE 6 — Dataset builders

Implemented:
- leakage-safe future target generation from feature frames
- supervised dataset builder from feature snapshots or flat feature frames
- lagged flat window construction for model-ready rows
- walk-forward train/validation/test splits with embargo support
- Parquet export helper for downstream training pipelines
- unit and integration tests for labeling, dataset assembly, and split behavior

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/labels.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/datasets.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/splits.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/__init__.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_data_labels.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_data_datasets.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/integration/test_walk_forward_splits.py`

### PHASE 7 — Transformer signal model

Implemented:
- transformer model configuration contract
- lagged feature tensorizer for PHASE 6 dataset rows
- baseline causal transformer encoder for supervised signal prediction
- thin predictor wrapper for dataframe and batch inference
- unit and integration tests for tensorization, causal masking, and dataset-to-model compatibility

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/models/__init__.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/models/config.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/models/tensorizer.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/models/transformer.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_models_tensorizer.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_models_transformer.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/integration/test_model_dataset_bridge.py`

### PHASE 8 — RL environment and policy training

Implemented:
- deterministic offline trading environment with explicit action and reward accounting
- categorical policy network for discrete short/flat/long actions
- rollout helpers for full-episode policy execution
- policy-gradient training helpers for baseline experimentation
- unit and integration tests for rewards, transitions, and multi-step episode behavior

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/rl/__init__.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/rl/config.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/rl/environment.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/rl/policy.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/rl/training.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_rl_environment.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_rl_policy_training.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/integration/test_rl_episode_rollout.py`

### PHASE 9 — Backtesting engine

Implemented:
- deterministic single-symbol event-driven backtesting engine
- target-position strategy interface over historical frames
- synthetic market `OrderIntent` and `FillEvent` generation with explicit spread, slippage, and commission costs
- netting position accounting with realized/unrealized pnl, equity, and drawdown tracking
- unit and integration tests for fill math, accounting transitions, and replay behavior
- blocking compatibility fix in `DomainModel` config for current `pydantic` usage

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/backtesting/__init__.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/backtesting/config.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/backtesting/accounting.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/backtesting/engine.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_backtesting_accounting.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/integration/test_backtesting_replay.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/domain/base.py`

### PHASE 10 — Walk-forward and robustness validation

Implemented:
- walk-forward validation orchestration over PHASE 6 dataset splits
- supervised-partition to backtest-frame conversion using `lag_000__` current features
- fold-level pnl, drawdown, turnover, and activity metrics
- aggregate robustness summaries across out-of-sample folds
- unit and integration tests for validation aggregation and end-to-end fold replay
- compatibility fixes in the local import path so targeted tests can run in the available Python 3.9.6 thread

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/validation/__init__.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/validation/metrics.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/validation/walk_forward.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_validation_metrics.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/integration/test_validation_walk_forward.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/integration/test_walk_forward_splits.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/datasets.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/splits.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/labels.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/bar_builders.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/data/collector.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/schema.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/offline.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/primitives.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/features/order_flow.py`

### PHASE 11 — Execution architecture

Implemented:
- broker-agnostic execution models and adapter protocol
- deterministic paper execution adapter with market, limit, stop, and stop-limit lifecycle handling
- paper/live execution router with paper as the default safe path
- explicit fill and position updates reusing the PHASE 9 accounting logic
- unit and integration tests for paper fills, routing, and end-to-end paper execution workflows

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/__init__.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/models.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/interfaces.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/paper.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/router.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_execution_paper.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/integration/test_execution_workflow.py`

### PHASE 12 — Deployment and operations

Implemented:
- deployment runtime for `research`, `paper`, and live-safe startup modes
- paper-safe live fallback with explicit live confirmation requirements
- in-memory health and Prometheus-style metrics surfaces
- deployment CLI entrypoint for runtime summary, health, and metrics inspection
- paper config overlay plus deployment/broker/monitoring config sections
- compatibility fallback in the config loader when `pydantic-settings` is unavailable in reduced local environments
- unit and integration tests for deployment safety defaults and paper-mode runtime wiring

Key files:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/deployment/__init__.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/deployment/entrypoints.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/deployment/runtime.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/deployment/health.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/deployment/metrics.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/scripts/run_runtime.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/configs/paper.yaml`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/config/models.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/config/loader.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/unit/test_deployment_runtime.py`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/tests/integration/test_deployment_bootstrap.py`

## Current State

We are at:
- POST-PHASE — Hardening, live integration refinement, and operational stabilization

Already available building blocks:
- canonical tick and book models
- replay/raw ingestion path
- raw Parquet writer
- streaming bar builders
- fractional differentiation
- offline and online feature snapshots
- stable microstructure feature naming
- leakage-safe supervised datasets
- walk-forward split utilities
- transformer signal model
- dataset-to-model tensorization bridge
- deterministic RL trading environment
- policy training scaffolds
- event-driven single-symbol backtesting engine
- explicit fill and netting accounting layer
- walk-forward validation orchestrator
- fold-level robustness summaries
- paper execution adapter and execution router
- explicit order lifecycle state
- deployment runtime bootstrap with paper-safe fallback
- operational health snapshots and metrics surfaces
- pure broker reconciliation helpers for orders and positions
- unified audit journal events, JSONL writer, and flat export records
- standalone OMS lifecycle helpers and deterministic pre-trade risk engine contracts
- canonical post-phase backlog in `docs/post-phase-roadmap.md`

Current repository status:
- phases 1-12 implemented
- README updated through PHASE 12
- tests were added for phases 1-12
- persistent session continuity workflow added via `scripts/handoff.py`
- persistent same-window checkpoint added via `SESSION_CHECKPOINT.md`
- post-phase roadmap added via `docs/post-phase-roadmap.md`
- P0.1 Sprint A+ operational foundation completed on 2026-04-27 with Makefile, safe GitHub Actions, repo tree, dev setup, test matrix, and interface map docs
- P0.2 MT5 Safe Submit Chain completed on 2026-04-27 with normalized `order_check` results and guarded `order_send`
- P0.3 Unified Event Journal Contract completed on 2026-04-27 with audit event envelope, JSONL writer, flat export, schema docs, and round-trip tests
- P0.4 OMS/RiskEngine State Machine completed on 2026-04-27 with lifecycle transitions, emergency flatten intent, deterministic risk blocks, and journalable risk decisions
- P0.5 Python 3.11+ Target Validation completed on 2026-04-27 using a local Python 3.12.13 `.venv`
- `python3 -m pytest` passed on 2026-03-28 with `109 passed`
- `python3 -m pytest` passed on 2026-04-27 with `109 passed` and no Pydantic warnings after logging/domain config cleanup
- `python3 -m pytest` passed on 2026-04-27 with `113 passed` after MT5 safe-submit hardening
- `python3 -m pytest` passed on 2026-04-27 with `119 passed` after unified journal hardening
- `python3 -m pytest` passed on 2026-04-27 with `136 passed` after OMS/RiskEngine hardening
- `make PYTHON=.venv/bin/python test` passed on 2026-04-27 with `136 passed` in Python 3.12.13
- `python3 -m compileall src tests scripts` passed on 2026-03-28
- `python3 -m pytest tests/unit/test_config_loader.py tests/unit/test_execution_mt5_client.py tests/unit/test_execution_mt5_live.py tests/integration/test_deployment_bootstrap.py` passed on 2026-03-28
- `python3 -m pytest tests/unit/test_execution_mt5_client.py tests/unit/test_deployment_mt5_preflight.py tests/unit/test_config_loader.py tests/integration/test_deployment_bootstrap.py` passed on 2026-03-28
- `python3 -m pytest tests/unit/test_deployment_runtime.py tests/unit/test_deployment_mt5_preflight.py` passed on 2026-03-28
- `python3 -m pytest tests/unit/test_config_loader.py tests/unit/test_execution_mt5_client.py tests/unit/test_execution_mt5_live.py tests/unit/test_execution_live_stub.py tests/unit/test_execution_reconciliation.py tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py` passed on 2026-03-28
- `python3 -m pytest tests/unit/test_execution_live_stub.py tests/unit/test_deployment_runtime.py` passed on 2026-03-28
- `python3 -m pytest tests/unit/test_execution_mt5_live.py tests/unit/test_deployment_runtime.py` passed on 2026-03-28
- `python3 -m pytest tests/unit/test_execution_mt5_live.py tests/unit/test_execution_live_stub.py tests/unit/test_execution_reconciliation.py tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py` passed on 2026-03-28
- `python3 -m pytest tests/unit/test_execution_live_stub.py tests/unit/test_execution_reconciliation.py tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py` passed on 2026-03-28
- `python3 -m pytest tests/unit/test_execution_reconciliation.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py` passed on 2026-03-28
- `python3 -m pytest tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py tests/unit/test_validation_metrics.py tests/integration/test_validation_walk_forward.py tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py` passed on 2026-03-27
- `python3 -m pytest tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_config_loader.py` passed on 2026-03-27
- `python3 -m pytest tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py tests/unit/test_validation_metrics.py tests/integration/test_validation_walk_forward.py tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py` passed on 2026-03-27
- `python3 -m pytest tests/unit/test_validation_metrics.py tests/integration/test_validation_walk_forward.py tests/integration/test_walk_forward_splits.py tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py` passed on 2026-03-27
- `python3 -m pytest tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py` passed on 2026-03-27
- `python3 -m pytest tests/unit/test_domain_trading.py tests/unit/test_rl_environment.py` passed on 2026-03-27
- `python3 scripts/run_runtime.py describe --config-name paper` passed on 2026-03-28
- `python3 scripts/run_runtime.py health --config-name paper` passed on 2026-03-28
- `python3 scripts/mt5_smoke.py --help` passed on 2026-03-28
- `python3 scripts/mt5_smoke.py --config-name mt5 --preflight-only` passed on 2026-03-28 and auto-discovered the local MT5 terminal path
- `python3 scripts/mt5_smoke.py --config-name mt5` now fails with structured JSON preflight diagnostics on 2026-03-28 when dependencies are missing
- repository-wide `pytest` now passes in the available Python 3.9.6 compatibility environment and in the Python 3.12.13 target-validation `.venv`

## Last Session Snapshot

- Session closed on: 2026-03-28
- Last completed implementation phase: PHASE 12
- Last completed post-phase hardening milestone:
  - P0.5 Python 3.11+ Target Validation is complete: local `.venv` uses Python 3.12.13, full dev/ml extras are installed, compile passes, and pytest passes with `136 passed`
  - P0.4 OMS/RiskEngine State Machine is complete: `scalper_ai.services.oms` now provides lifecycle transition validation and emergency flatten intents, while `scalper_ai.risk.engine` provides deterministic pre-trade risk checks and journalable risk decisions
  - P0.3 Unified Event Journal Contract is complete: `scalper_ai.journal` now provides `JournalEvent`, `JournalEventType`, JSONL writer/reader, flat record export, and `docs/event-schema.md`
  - P0.2 MT5 Safe Submit Chain is complete: `Mt5TerminalClient.submit_order()` now runs `order_check` before `order_send`, failed or unavailable checks return structured rejections, and fake-module tests cover check/send paths
  - stale project-memory paths were updated to `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai`
  - Pydantic warning cleanup was completed for logging config field naming and domain model JSON config
  - repository-wide `pytest` passed in the available Python 3.9.6 environment with `109 passed` and no Pydantic warnings before P0.2
  - repository-wide `pytest` now passes in the available Python 3.9.6 environment with `136 passed`
  - pure execution reconciliation helpers were added under `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/reconciliation.py`
  - reconciliation is now wired into `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/deployment/runtime.py` health and metrics surfaces
  - broker snapshot contract and internal execution state tracker were added under `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/snapshots.py`
  - concrete live-facing stub adapter was added under `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/live_stub.py`
  - broker connectivity snapshot contract was added under `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/connectivity.py`
  - live-path broker dependency checks are now wired into `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/deployment/runtime.py`
  - MT5 live execution adapter and real terminal client were added under `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/mt5_live.py` and `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/execution/mt5_client.py`
  - MT5 preflight helpers were added under `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/deployment/mt5_preflight.py`
  - MT5 config overlay and smoke script were upgraded under `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/configs/mt5.yaml` and `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/scripts/mt5_smoke.py` with terminal auto-discovery and structured preflight diagnostics
  - runtime can now auto-reuse a live adapter itself as the broker snapshot provider when the adapter exposes broker snapshot methods
- Session continuity support is now in place:
  - `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/AGENT_HANDOFF.md`
  - `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/SESSION_CHECKPOINT.md`
  - `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/scripts/handoff.py`
- PHASE 12 is implemented under:
  - `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/src/scalper_ai/deployment/`
  - `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/scripts/run_runtime.py`
- The exact next task is post-phase hardening:
  - start real MT5 terminal validation when the `MetaTrader5` package, terminal, credentials, and explicit live confirmation are available
  - if MT5 validation remains unavailable, continue with `P1.1 — Execution-Aware Simulator V2` in `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/post-phase-roadmap.md`

## Constraints To Preserve

- no look-ahead bias
- no target leakage
- timestamps must stay UTC-aware
- costs must remain explicit
- paper mode by default
- adapters separated from domain logic
- offline and online pipelines separated
- pure feature functions where possible
- no hidden global state

## Post-Phase Focus

- validate the MT5-backed client against a real installed terminal and credentials
- refine live execution readiness beyond the current paper-safe runtime boundary and reuse the reconciliation helpers as the comparison layer
- add deeper broker reconciliation, dependency supervision, and long-running runtime hardening
- keep the PHASE 12 deployment wrapper as the single startup and observability surface

## Suggested Next Prompt For A New Chat

Use something like:

> Read `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/AGENTS.md`, `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/AGENT_HANDOFF.md`, `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/current-state.md`, `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/todo-next.md`, and `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/SESSION_CHECKPOINT.md`. Then inspect the repository and continue from the post-phase hardening roadmap only. Preserve the completed PHASE 1-12 layers unless a blocking bug is found.

Optional stronger version:

> Read `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/AGENTS.md`, `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/AGENT_HANDOFF.md`, `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/current-state.md`, `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/todo-next.md`, and `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/SESSION_CHECKPOINT.md`. Then inspect the repo. All numbered phases are complete. Continue with production-minded hardening, live integration refinement, and operational stabilization without redoing completed phase work unless a blocking bug is found.

Best resume command sequence:

```bash
cd '/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai'
python3 scripts/handoff.py status
python3 scripts/handoff.py prompt
```

## Validation Notes

In this thread, the following checks passed:

```bash
cd '/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai'
python3 -m compileall src tests scripts
python3 -m pytest
python3 -m pytest tests/unit/test_execution_reconciliation.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py
python3 -m pytest tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_config_loader.py
python3 -m pytest tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py tests/unit/test_validation_metrics.py tests/integration/test_validation_walk_forward.py tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py
python3 -m pytest tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py tests/unit/test_validation_metrics.py tests/integration/test_validation_walk_forward.py tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py
python3 -m pytest tests/unit/test_validation_metrics.py tests/integration/test_validation_walk_forward.py tests/integration/test_walk_forward_splits.py tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py
python3 -m pytest tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py
python3 -m pytest tests/unit/test_domain_trading.py tests/unit/test_rl_environment.py
python3 scripts/run_runtime.py describe --config-name paper
python3 scripts/run_runtime.py health --config-name paper
```

The system `python3` available in this thread is still Python 3.9.6, while the project requires Python 3.11+. A local `.venv` now uses bundled Python 3.12.13, has `.[dev,ml]` installed, and passes the full suite.

## Handoff Rule

The next agent should:
- inspect this file first
- inspect `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/post-phase-roadmap.md`
- inspect the current repo tree
- continue from the post-phase roadmap only
- avoid rewriting earlier phases unless a bug is found that blocks the current hardening task

## Session Protocol

When the user says:
- `мы закончили`

The active agent should:
- update this file
- refresh the `Completed Phases` and `Current State` sections
- record what was finished in the just-completed session
- record the exact next phase or next task to continue from
- keep the `Suggested Next Prompt For A New Chat` current

To resume quickly in a new terminal/chat context, use:

```bash
cd '/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai'
python3 scripts/handoff.py path
python3 scripts/handoff.py status
python3 scripts/handoff.py checkpoint
python3 scripts/handoff.py prompt
```

Meaning of commands:
- `path` prints the absolute path to the handoff file
- `checkpoint-path` prints the absolute path to the persistent checkpoint file
- `status` prints the current state block only
- `checkpoint` prints the persistent same-window checkpoint
- `prompt` prints a ready-to-paste resume prompt for a new Codex window
- `show` prints the full handoff document

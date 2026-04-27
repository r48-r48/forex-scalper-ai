# Current State

## What Already Works

- PHASE 1 bootstrap is complete:
  - typed config
  - logging
  - Docker Redis
  - baseline tests
- PHASE 2 canonical domain layer is complete:
  - `TickEvent`
  - `BookSnapshot`
  - `FeatureSnapshot`
  - `OrderIntent`
  - `FillEvent`
  - `PositionState`
- PHASE 3 ingestion layer is complete:
  - replay JSONL/Parquet readers
  - MT5 ingestion scaffolds
  - batching
  - buffered raw Parquet writing
- PHASE 4 preprocessing layer is complete:
  - `BarEvent`
  - time bars
  - tick bars
  - volatility bars
  - imbalance bars
  - fractional differentiation
- PHASE 5 feature engineering is complete:
  - stable feature schema and naming
  - spread / return / realized volatility primitives
  - quote intensity
  - OFI and MLOFI
  - VPIN-like toxicity proxy
  - macro placeholder provider
  - offline and online feature calculators
  - parity-oriented tests
- PHASE 6 dataset builders are complete:
  - leakage-safe target generation
  - lagged supervised dataset builders
  - walk-forward splits with embargo support
  - Parquet export helper
  - unit and integration tests for labeling and splitting
- PHASE 7 transformer model layer is complete:
  - transformer config contract
  - lagged feature tensorizer
  - baseline causal transformer signal model
  - predictor wrapper for inference
  - passing model-layer unit and integration tests
- PHASE 8 RL environment and policy layer are complete:
  - deterministic offline trading environment
  - explicit action and reward accounting
  - categorical policy network
  - rollout and policy-gradient training helpers
  - passing RL-layer unit and integration tests
- PHASE 9 backtesting layer is complete:
  - deterministic event-driven replay engine
  - target-position strategy interface
  - synthetic market order and fill simulation with explicit costs
  - netting position accounting with equity / pnl / drawdown tracking
  - passing PHASE 9 unit and integration tests
- PHASE 10 validation layer is complete:
  - walk-forward orchestration over PHASE 6 splits and PHASE 9 backtests
  - fold-level metrics and aggregate robustness summaries
  - supervised-partition to backtest-frame conversion for lagged datasets
  - passing PHASE 10 unit and integration tests
- PHASE 11 execution layer is complete:
  - broker-agnostic execution models and adapter protocol
  - deterministic paper execution adapter with explicit order lifecycle handling
  - router for paper/live adapter boundaries
  - passing PHASE 11 unit and integration tests
- PHASE 12 deployment and operations layer is complete:
  - deployment runtime for research, paper, and live-safe startup modes
  - paper-safe fallback when live prerequisites are not satisfied
  - health snapshots and Prometheus-style metrics surfaces
  - deployment CLI entrypoint and config overlays for paper/live-safe startup
  - passing PHASE 12 unit and integration tests
- Persistent session continuity now exists:
  - `AGENT_HANDOFF.md`
  - `SESSION_CHECKPOINT.md`
  - `scripts/handoff.py`
  - `AGENTS.md`
- Post-phase hardening progress:
  - pure broker reconciliation helpers were added for order and position state comparison
  - reconciliation is now wired into deployment/runtime health and metrics surfaces
  - broker snapshot contract and internal execution state tracker were added for reconciliation wiring
  - concrete live-facing stub adapter now implements the broker snapshot contract end-to-end
  - broker connectivity snapshot contract and live-path dependency checks were added to the runtime health and metrics surfaces
  - MT5 live execution path now includes a real terminal client wrapper, config/env wiring, auto bootstrap factory, terminal-path auto-discovery, and a structured preflight-aware smoke script
  - runtime can now auto-reuse a live adapter itself as the broker snapshot provider when the adapter exposes the reconciliation contract
  - stale workspace paths in project memory/docs were updated to `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai`
  - Pydantic warning cleanup was completed for logging config field naming and domain model JSON config
  - Sprint A+ operational foundation was added:
    - `Makefile`
    - `.github/workflows/ci.yml`
    - `docs/repo-tree.md`
    - `docs/dev-setup.md`
    - `docs/test-matrix.md`
    - `docs/interfaces.md`
  - MT5 safe submit chain was hardened so `Mt5TerminalClient.submit_order()` runs `order_check` before `order_send` and returns structured rejections when checks fail or are unavailable
  - unified event journal contracts were added with JSONL audit writing and flat Parquet-friendly export records
  - repository-wide `pytest` now passes in the available Python 3.9.6 environment with `119 passed`

## Current Problem / Current Focus

- PHASE 1-12 are now implemented.
- The repository now has research, validation, execution, deployment, early reconciliation hardening, MT5 pre-send safety, and unified journal layers.
- The next work should focus on OMS/RiskEngine state-machine hardening, true Python 3.11+ validation, live adapter refinement, and operational stabilization.

## Important Constraints

- No look-ahead bias
- No target leakage
- UTC-aware timestamps only
- Explicit spread/slippage/cost awareness
- Offline and online feature parity
- Graceful degradation when L2/DOM is absent

## Current Environment Note

- In this session, local project dependencies were not installed in the system Python.
- The available `python3` in this thread is still Python 3.9.6 even though the project target remains Python 3.11+.
- `python3 -m compileall src tests scripts` passed.
- `PYTHONPYCACHEPREFIX=/tmp/scalper_ai_pycache python3 -m compileall src tests scripts` passed on 2026-04-27.
- `python3 -m pytest` passed on 2026-04-27 with `119 passed` after P0.3 unified journal hardening.
- `make compile` passed on 2026-04-27.
- `make test` passed on 2026-04-27 with `119 passed`.
- `python3 -m pytest tests/unit/test_journal_events.py tests/integration/test_journal_jsonl.py` passed on 2026-04-27 with `6 passed`.
- `python3 -m pytest tests/unit/test_execution_mt5_client.py tests/integration/test_deployment_bootstrap.py tests/unit/test_execution_mt5_live.py` passed on 2026-04-27 with `12 passed`.
- `make run-paper` passed on 2026-04-27.
- `make health-paper` passed on 2026-04-27 with overall `pass`.
- `make mt5-preflight` passed on 2026-04-27 with structured missing-dependency diagnostics.
- `python3 -m pytest tests/unit/test_config_loader.py tests/unit/test_execution_mt5_client.py tests/unit/test_execution_mt5_live.py tests/integration/test_deployment_bootstrap.py` passed.
- `python3 -m pytest tests/unit/test_execution_mt5_client.py tests/unit/test_deployment_mt5_preflight.py tests/unit/test_config_loader.py tests/integration/test_deployment_bootstrap.py` passed.
- `python3 -m pytest tests/unit/test_deployment_runtime.py tests/unit/test_deployment_mt5_preflight.py` passed.
- `python3 -m pytest tests/unit/test_config_loader.py tests/unit/test_execution_mt5_client.py tests/unit/test_execution_mt5_live.py tests/unit/test_execution_live_stub.py tests/unit/test_execution_reconciliation.py tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py` passed.
- `python3 -m pytest tests/unit/test_execution_live_stub.py tests/unit/test_deployment_runtime.py` passed.
- `python3 -m pytest tests/unit/test_execution_mt5_live.py tests/unit/test_deployment_runtime.py` passed.
- `python3 -m pytest tests/unit/test_execution_mt5_live.py tests/unit/test_execution_live_stub.py tests/unit/test_execution_reconciliation.py tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py` passed.
- `python3 -m pytest tests/unit/test_execution_live_stub.py tests/unit/test_execution_reconciliation.py tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py` passed.
- `python3 -m pytest tests/unit/test_execution_reconciliation.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py` passed.
- `python3 -m pytest tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py tests/unit/test_validation_metrics.py tests/integration/test_validation_walk_forward.py tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py` passed.
- `python3 -m pytest tests/unit/test_deployment_runtime.py tests/integration/test_deployment_bootstrap.py tests/unit/test_config_loader.py` passed.
- `python3 -m pytest tests/unit/test_execution_paper.py tests/integration/test_execution_workflow.py tests/unit/test_validation_metrics.py tests/integration/test_validation_walk_forward.py tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py` passed.
- `python3 -m pytest tests/unit/test_validation_metrics.py tests/integration/test_validation_walk_forward.py tests/integration/test_walk_forward_splits.py tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py` passed.
- `python3 -m pytest tests/unit/test_backtesting_accounting.py tests/integration/test_backtesting_replay.py` passed.
- `python3 -m pytest tests/unit/test_domain_trading.py tests/unit/test_rl_environment.py` passed.
- `python3 scripts/run_runtime.py describe --config-name paper` passed.
- `python3 scripts/run_runtime.py health --config-name paper` passed.
- `python3 scripts/mt5_smoke.py --help` passed.
- `python3 scripts/mt5_smoke.py --config-name mt5 --preflight-only` passed and auto-discovered the local MT5 terminal bundle path.
- `python3 scripts/mt5_smoke.py --config-name mt5` now exits with structured JSON preflight diagnostics instead of a raw traceback when dependencies are missing.
- Full repository-wide `pytest` now passes in the available Python 3.9.6 environment, but the target-environment validation in Python 3.11+ is still pending because this host does not provide a 3.11 interpreter or a local toolchain to install one quickly.

## Exact Next Step

Move to post-phase hardening:
- implement P0.4 OMS/RiskEngine State Machine from `docs/post-phase-roadmap.md`
- provision a real Python 3.11+ environment and re-run repository-wide validation there
- install the `MetaTrader5` Python package plus real `SCALPER_AI_BROKER_MT5_*` credentials, then run `python3 scripts/mt5_smoke.py --config-name mt5 --preflight-only` followed by `python3 scripts/mt5_smoke.py --config-name mt5`
- validate the new MT5 terminal client against an actual installed terminal, especially broker-side polling, history/deal normalization, and partial-fill behavior
- add deeper operational hardening such as long-running service supervision, alerting, and dependency checks beyond the current connectivity layer

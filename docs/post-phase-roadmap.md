# Post-Phase Roadmap

## Purpose

This is the persistent execution plan for the POST-PHASE hardening work.
It translates the two external research reports plus the current repository state into an ordered backlog.

Read this file after:
- `AGENTS.md`
- `AGENT_HANDOFF.md`
- `docs/current-state.md`
- `docs/todo-next.md`
- `SESSION_CHECKPOINT.md`

## Source Inputs

- Current repository state: PHASE 1-12 complete, `.venv/bin/pytest` passed with `157 passed` on 2026-04-28 in the Python 3.12.13 target-validation environment.
- External report: `/Users/dzhabrailtalkanov/Downloads/deep-research-report.md`
- External report: `/Users/dzhabrailtalkanov/Downloads/мм.md`

The reports agree on the main direction:
- do not treat the project as a black-box AI model
- build a trading platform first: data plane, execution plane, risk/OMS, event journal, validation gate, observability
- keep MT5 as the nearest practical MVP path
- use ML/RL only after realistic replay and execution logging are reliable

## Current Baseline

Already implemented:
- canonical immutable domain models for ticks, books, bars, features, orders, fills, and positions
- replay/raw ingestion and raw Parquet writer
- streaming bar builders and preprocessing
- offline and online feature calculators with parity tests
- leakage-safe targets, supervised datasets, walk-forward splits
- baseline Transformer signal model and tensorizer
- deterministic RL environment and policy training helpers
- event-driven backtesting V1 with explicit spread/slippage/commission costs
- walk-forward validation
- paper execution adapter and execution router
- broker reconciliation helpers and snapshot contracts
- deployment runtime with paper-safe fallback, health checks, and Prometheus-style metrics
- MT5 terminal client, live adapter skeleton, preflight checks, and smoke script
- unified event journal envelope, JSONL audit writer, and flat Parquet-friendly export records
- standalone OMS lifecycle helpers and deterministic pre-trade RiskEngine contracts
- execution-aware simulator V2 with latency, partial-fill, queue, stale/closed-market, reject, cancel, and cancel/replace race scenarios
- baseline strategy suite with spread/mean-reversion, OFI/imbalance, volatility-breakout, cost sensitivity, and walk-forward reports
- unified validation gate reports for backtest, walk-forward, execution stress, latency/slippage, risk flags, and regime breakdown
- paper/shadow champion-challenger decision reporting without broker order submission
- interpretable supervised baseline filter with leakage-safe walk-forward evaluation
- observability alert-rule docs, platform roadmap, production checklist, incident/postmortem templates, and release/incident runbooks

Known gaps:
- no real MT5 terminal/package/credentials validation yet
- no Docker/Compose runtime image yet
- no real alert transport or OpenTelemetry trace path yet
- no dedicated full-repo Ruff/mypy cleanup baseline yet

## Roadmap Status

- P0.1 Sprint A+ Operational Foundation: completed on 2026-04-27.
- P0.2 MT5 Safe Submit Chain: completed on 2026-04-27.
- P0.3 Unified Event Journal Contract: completed on 2026-04-27.
- P0.4 OMS/RiskEngine State Machine: completed on 2026-04-27.
- P0.5 Python 3.11+ Target Validation: completed on 2026-04-27 with Python 3.12.13.
- P1.1 Execution-Aware Simulator V2: completed on 2026-04-27.
- P1.2 Baseline Strategy Suite: completed on 2026-04-28.
- P1.3 Unified Validation Report: completed on 2026-04-28.
- P1.4 Paper And Shadow Mode: completed on 2026-04-28.
- P2.1 Supervised Baseline Filter: completed on 2026-04-28.
- P2.2 Observability Expansion: completed on 2026-04-28 as documentation and roadmap.
- P2.3 Release Runbooks: completed on 2026-04-28 as documented procedures and templates.
- Current next task: real MT5 terminal validation when package/terminal/credentials are available.

## P0 Workstream

### P0.1 — Sprint A+ Operational Foundation

Status: completed on 2026-04-27.

Goal: make the repository reproducible and easy to resume before deeper execution changes.

Tasks:
- Create `docs/repo-tree.md` with the current project tree up to depth 4.
- Create `docs/dev-setup.md` with exact install, test, compile, runtime, and MT5 preflight commands.
- Create `docs/test-matrix.md` listing unit/integration groups, current status, external dependency notes, and risk.
- Create `docs/interfaces.md` describing the already existing contracts and the target contracts:
  - `MarketDataAdapter`
  - `Strategy`
  - `RiskEngine`
  - `OMS`
  - `BrokerAdapter`
  - `Journal`
  - `PortfolioService`
- Add `Makefile` commands:
  - `install`
  - `test`
  - `compile`
  - `lint`
  - `typecheck`
  - `run-paper`
  - `health-paper`
  - `mt5-preflight`
- Add GitHub Actions workflow for Python 3.11:
  - compile
  - unit/integration tests that do not need real MT5 credentials
  - no live trading or secret-dependent checks

Definition of done:
- `make test` passes locally in the available environment.
- `make compile` passes locally.
- CI file exists and is safe for GitHub without secrets.
- A new engineer can understand how to run the project without reading chat history.

### P0.2 — MT5 Safe Submit Chain

Status: completed on 2026-04-27.

Goal: harden live-order submission before real-terminal validation.

Tasks:
- Add `order_check` support to the MT5 protocol wrapper.
- Ensure MT5 live order submission follows:
  - build normalized request
  - `order_check`
  - record check result
  - `order_send`
  - normalize result
  - reconcile broker/internal state
- Add typed normalized MT5 check result model.
- Add tests for:
  - check success then send success
  - check rejection prevents send
  - check unavailable returns structured rejection
  - send failure after successful check is journalable/reconcilable

Definition of done:
- MT5 client can be tested with fake module for check/send paths.
- No unchecked live order is sent through the MT5 client path.
- Existing MT5 adapter tests still pass.

Completed implementation notes:
- `MetaTrader5ModuleProtocol` now includes `order_check`.
- `Mt5TerminalClient.submit_order()` builds one normalized payload, runs `order_check`, and refuses `order_send` when the check is rejected or unavailable.
- `Mt5OrderCheckResult` normalizes accepted/rejected status, retcode, broker comment, timestamp, and margin/account fields.
- Unit and integration fakes cover check success, check rejection, unavailable check result, and send failure after a successful check.
- Full journal persistence and cross-event correlation continue in P0.3, using this safe client path as the broker-order boundary.

### P0.3 — Unified Event Journal Contract

Status: completed on 2026-04-27.

Goal: make every market decision and execution event replayable and auditable.

Tasks:
- Add journal/event contracts without duplicating existing domain models unnecessarily.
- Required event categories:
  - `market_data_event`
  - `signal_event`
  - `order_request_event`
  - `order_response_event`
  - `fill_event`
  - `position_snapshot`
  - `risk_event`
  - `latency_event`
- Keep all timestamps UTC-aware.
- Add JSONL audit writer.
- Add Parquet-friendly record export, reusing existing raw writer patterns where practical.
- Create `docs/event-schema.md`.
- Add smoke tests for write/read round trips.

Definition of done:
- A strategy decision can be tied to a market event, an order request, a broker response, fills, and resulting position state.
- Journal writers do not require live broker access.

Completed implementation notes:
- Added `scalper_ai.journal` with `JournalEvent`, `JournalEventType`, JSONL writer/reader, and flat record export.
- Journal categories cover market data, signal, order request/response, fill, position snapshot, risk, and latency events.
- Journal payloads reuse existing domain `to_record()` output or normalized mappings instead of duplicating domain contracts.
- Added `docs/event-schema.md`.
- Added unit and integration round-trip tests for event contracts and JSONL persistence.

### P0.4 — OMS/RiskEngine State Machine

Status: completed on 2026-04-27.

Goal: move from adapter-level safety to a real execution control layer.

Tasks:
- Define order lifecycle state machine:
  - `NEW`
  - `CHECKED`
  - `SENT`
  - `ACK`
  - `PARTIAL`
  - `FILLED`
  - `REJECTED`
  - `CANCELLED`
  - `RECONCILED`
- Add risk controls:
  - max position
  - max daily loss
  - max order rate
  - duplicate intent/order detection
  - symbol kill switch
  - session kill switch
  - reject-burst kill switch
  - stale-market-data kill switch
- Add emergency flatten workflow.
- Add `docs/oms-risk.md` and a runbook section.
- Add deterministic tests for transitions and risk blocks.

Definition of done:
- Invalid state transitions are impossible or rejected.
- Kill switch behavior is deterministic.
- Risk decisions are logged as journalable events.

Completed implementation notes:
- Added `scalper_ai.services.oms` with immutable OMS order records, lifecycle transition validation, and emergency flatten intent generation.
- Added `scalper_ai.risk.engine` with deterministic pre-trade risk limits, context, decisions, reject codes, and journalable risk-event rendering.
- Implemented controls for session/symbol kill switches, duplicate intent/order detection, reject bursts, stale market data, order rate, daily loss/drawdown, max position, and reduce-only exposure growth.
- Added `docs/oms-risk.md`.
- Added deterministic unit tests for OMS transitions, emergency flattening, and required risk blocks.

### P0.5 — Python 3.11+ Target Validation

Status: completed on 2026-04-27 with Python 3.12.13.

Goal: validate the project in the declared runtime, not only the local Python 3.9.6 host.

Tasks:
- Provision or use Python 3.11+.
- Install `pip install -e ".[dev,ml]"`.
- Run:
  - `python -m compileall src tests scripts`
  - `python -m pytest`
  - targeted MT5 preflight tests that do not require credentials
- Record results in `docs/current-state.md` and `SESSION_CHECKPOINT.md`.

Definition of done:
- Full suite passes in Python 3.11+ or failures are classified as environment, genuine bug, flaky, or missing fixture.

Completed validation notes:
- Created a local `.venv` from the bundled Python 3.12.13 runtime.
- Installed `pip install -e ".[dev,ml]"`.
- `make PYTHON=.venv/bin/python compile` passed.
- `make PYTHON=.venv/bin/python test` passed with `136 passed`.
- `make PYTHON=.venv/bin/python mt5-preflight` passed with structured missing-dependency diagnostics for the absent `MetaTrader5` package, terminal credentials, and live confirmation.
- Fixed a target-env config loader bug so `SCALPER_AI_*` env overrides behave the same with and without `pydantic-settings` installed.
- After P1.1, the same Python 3.12.13 `.venv` full suite passes with `141 passed`.

## P1 Workstream

### P1.1 — Execution-Aware Simulator V2

Status: completed on 2026-04-27.

Goal: extend backtesting beyond immediate market fills.

Tasks:
- Add latency injection.
- Add partial-fill scenarios.
- Add cancel/replace race scenarios.
- Add stale book and market status scenarios.
- Add queue-position proxy where DOM is available.
- Add explicit fill ratio, cancel ratio, reject ratio, and slippage metrics.

Definition of done:
- Forced scenarios have tests.
- Backtest reports include execution-quality metrics, not only pnl/drawdown.

Completed implementation notes:
- Added `scalper_ai.backtesting.execution_simulator` with `run_execution_aware_backtest`.
- V2 supports latency steps, partial-fill ratios, available-liquidity caps, queue-ahead proxy, forced rejects/cancels, stale-market-data rejections, closed-market rejections, and cancel/replace race fills.
- Added execution-quality metrics for fill ratio, cancel ratio, reject ratio, slippage cost, average slippage bps, and average latency steps.
- Added `docs/execution-simulator-v2.md`.
- Added forced-scenario tests in `tests/unit/test_backtesting_execution_simulator.py`.

### P1.2 — Baseline Strategy Suite

Status: completed on 2026-04-28.

Goal: compare ML/RL against real baselines.

Tasks:
- Add strategy interface if the existing backtest protocol is not enough.
- Implement baselines:
  - spread/mean-reversion micro baseline
  - OFI/imbalance baseline
  - volatility breakout/momentum micro baseline
- Add configs and walk-forward reports.
- Add sensitivity analysis for fees/slippage/risk limits.

Definition of done:
- Every future ML model has a baseline suite to beat.

Completed implementation notes:
- Reused the existing `TargetPositionStrategy` protocol; no parallel strategy contract was added.
- Added `scalper_ai.backtesting.baselines` with spread/mean-reversion, OFI/imbalance, and volatility-breakout strategies.
- Added `scalper_ai.validation.baseline_suite` with backtest suite summaries, explicit cost/risk sensitivity scenarios, and walk-forward report frames.
- Added `configs/baselines.yaml` and `docs/baseline-strategies.md`.
- Added unit/integration tests for strategy behavior, report generation, sensitivity scenarios, and walk-forward baseline reports.

### P1.3 — Unified Validation Report

Status: completed on 2026-04-28.

Goal: create a go/no-go artifact before paper/live changes.

Tasks:
- Build one report covering:
  - backtest
  - walk-forward
  - stress replay
  - latency/slippage
  - risk flags
  - regime breakdown
- Save reports under `data/artifacts` or a dedicated reports path while respecting `.gitignore`.
- Add `docs/validation-gate.md`.

Definition of done:
- New strategies cannot be promoted without a validation report.

Completed implementation notes:
- Added `scalper_ai.validation.gate` with `ValidationGateReport`, configurable thresholds, pass/warn/fail checks, risk flags, latency/slippage summary, and regime breakdown.
- Added JSON artifact persistence through `write_validation_gate_report`.
- Added `docs/validation-gate.md`.
- Added unit tests for complete offline artifact bundles, risk-flag failures, timezone validation, and JSON persistence.

### P1.4 — Paper And Shadow Mode

Status: completed on 2026-04-28.

Goal: use the same signal/risk/OMS path in replay, paper, shadow, and live-safe modes.

Tasks:
- Add champion/challenger decision logging.
- Add decision diff report.
- Add daily session report.
- Ensure paper/shadow differences are config-only where possible.

Definition of done:
- Challenger can run without sending orders and produce decision deltas.

Completed implementation notes:
- Added `scalper_ai.validation.shadow` for champion/challenger decision-only replay.
- Shadow reports record resolved targets, raw target output, current champion shadow position, absolute deltas, and direction-change ratios.
- Shadow mode does not create orders and does not touch broker adapters.
- Added `docs/shadow-mode.md`.
- Added unit tests for decision drift summaries, JSON persistence, strategy-name validation, and UTC-aware generation time.

## P2 Workstream

### P2.1 — Supervised Baseline Filter

Status: completed on 2026-04-28.

Goal: add ML as a filter/challenger after baselines exist.

Tasks:
- Start with simple, interpretable models before larger sequence models.
- Preserve feature parity between training and online paths.
- Use leakage-safe labels and walk-forward evaluation.
- Defer DVC/MLflow until datasets and model artifacts exist.

Definition of done:
- ML result is compared against baseline strategies and validation gates.

Completed implementation notes:
- Added `scalper_ai.models.baseline_filter` with a transparent centroid-difference directional model, score/predict methods, and feature-importance output.
- Added `scalper_ai.validation.supervised_filter` for leakage-safe walk-forward fitting on train partitions and evaluation on test partitions.
- Added `docs/supervised-baseline-filter.md`.
- Added unit and integration tests for fitting, thresholds, directional-class validation, and out-of-sample walk-forward reporting.

### P2.2 — Observability Expansion

Status: completed on 2026-04-28 as documentation and platform roadmap.

Goal: grow from health/metrics surfaces to production operations.

Tasks:
- Add alert-rule documents for:
  - broker disconnect
  - stale data
  - reconciliation drift
  - reject burst
  - kill switch activation
- Add OpenTelemetry only when there is a real service boundary or trace path to instrument.
- Add `docs/platform-roadmap.md` for Docker/Compose first, Kubernetes/Helm/Argo later.

Definition of done:
- Operators know what the metrics mean and what action to take.

Completed implementation notes:
- Added `docs/alert-rules.md` for broker disconnect, stale data, reconciliation drift, reject burst, and kill switch activation.
- Added `docs/platform-roadmap.md` with Docker/Compose first, service boundaries later, and Kubernetes/Helm/Argo deferred.
- Alert transport and OpenTelemetry remain intentionally deferred until service boundaries exist.

### P2.3 — Release Runbooks

Status: completed on 2026-04-28.

Goal: make operation procedural, not improvised.

Tasks:
- Create runbooks:
  - market open
  - market close
  - broker disconnect
  - stale data
  - reject burst
  - emergency flatten
  - rollback release
- Add postmortem template.
- Add incident template.
- Add `docs/production-checklist.md`.

Definition of done:
- A later operator or agent can follow documented steps in normal and incident modes.

Completed implementation notes:
- Added runbooks for market open, market close, broker disconnect, stale data, reject burst, emergency flatten, and rollback release under `docs/runbooks/`.
- Added `docs/incident-template.md`, `docs/postmortem-template.md`, and `docs/production-checklist.md`.

## Do Not Do Yet

- Do not make RL the first production trading brain.
- Do not introduce Kubernetes/Helm/Argo before Compose/CI/runtime operations are stable.
- Do not add DVC/MLflow/Feast before real datasets and model artifacts exist.
- Do not create parallel schemas that duplicate the existing `domain` layer without a migration reason.
- Do not add another broker before the MT5 path is validated and journaled.
- Do not weaken paper-first live safety.

## Immediate Next Task

Start with real MT5 terminal validation when package, terminal, credentials, and explicit live confirmation are available.

Recommended first implementation slice:
1. Install the `MetaTrader5` Python package in the target runtime.
2. Configure terminal path or validate auto-discovery.
3. Configure `SCALPER_AI_BROKER_MT5_*` credentials or intentionally use a saved terminal session.
4. Set explicit live confirmation before any true live startup.
5. Run MT5 preflight and smoke checks against the real terminal.

If real MT5 validation remains unavailable, the next non-MT5 work is platform cleanup:
1. Add a Docker/Compose runtime image around the paper-safe default.
2. Create a full-repo Ruff/mypy cleanup baseline.
3. Wire alert-rule documents to an actual transport after the runtime topology is fixed.

Then run:

```bash
make compile
make test
make mt5-preflight
```

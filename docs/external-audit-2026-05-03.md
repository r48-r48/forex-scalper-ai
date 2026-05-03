# External Audit Triage - 2026-05-03

## Source

- Report: `/Users/dzhabrailtalkanov/Downloads/deep-research-report (2).md`
- Reviewed on: `2026-05-03`
- Scope: static production-readiness review of the Forex scalper project as a live/demo trading system.

## Executive Verdict

The report is directionally correct: the repository is a strong production scaffold, but it is not ready for real-money live trading. The current codebase is suitable for research, paper runtime supervision, MT5 diagnostics, safe broker probes, and continued hardening. It is not yet a complete autonomous Forex bot with a full market-data, feature, model inference, signal, risk, OMS, broker execution, reconciliation, and recovery loop.

The report was based on a static archive review and did not see all subsequent local validation evidence. Several findings are already closed in the current repository, but the core warning remains valid: do not treat paper runtime or MT5 probes as a production live strategy daemon.

## Findings Rechecked Against Current Code

### Confirmed And Fixed In This Slice

1. `configs/live.yaml` named an unsupported live adapter.
   - The file used `broker.live_adapter: external`, while `resolve_live_adapter_factory()` only supports `stub` and `mt5`.
   - Fixed on `2026-05-03`: `configs/live.yaml` now selects `mt5`, requires broker-side stop loss for MT5 orders, and disables paper fallback for the live overlay so a failed live startup cannot silently look like a live deployment.

2. Live startup could run without dependency providers.
   - Runtime health warned when live data/model/guard providers were missing, but startup itself did not fail closed.
   - Fixed on `2026-05-03`: live startup now requires `data_freshness_provider`, `model_health_provider`, and a `guard_state_provider` whenever news or volatility risk guards are enabled.

3. Daily drawdown risk context used current equity as both start and current equity.
   - `_build_risk_context()` used `_last_equity_by_route` for both `starting_equity` and `current_equity`, which could make the daily drawdown check ineffective.
   - Fixed on `2026-05-03`: runtime now tracks a separate UTC day-start equity per paper/live route and resets the baseline on UTC day boundaries.

### Already Closed Or Partly Outdated

1. The report says tests were not run.
   - The current repo has a green local gate: Ruff, mypy, compileall, full pytest, and safe MT5 preflight are wired into `make ci`.

2. The report says MT5 is only a partial diagnostic path.
   - This remains true for end-to-end autonomous trading, but the MT5 execution core is much stronger now: safe `order_check` before `order_send`, symbol spec enforcement, hedging-aware position handling, deal attribution, protective SL/TP validation and repair, reconnect supervision, startup reconciliation, and Parallels demo validation all exist.

3. The report flags dirty ZIP contents such as `.venv`, `.git`, cache files, and artifacts.
   - This is an export hygiene issue, not a committed repository issue. The release process still needs a clean source-archive step.

4. The report says health/model/data providers are absent.
   - Concrete runtime provider trackers now exist and can be wired into live or paper loops, but there is still no complete autonomous signal/inference loop using them end to end.

## Still Valid High-Priority Gaps

1. There is no complete long-running trading loop:
   `market data -> online features -> model inference -> signal gating -> risk -> OMS -> broker submit -> reconciliation -> recovery`.

2. The production training/model bundle pipeline is still incomplete:
   the first supervised baseline training/export CLI and runtime inference package now exist, but a model registry, drift monitoring, transformer training/export, and end-to-end live signal loop are still pending.

3. Backtesting remains insufficient for serious FX scalping:
   the current research backtest now has bid/ask execution plus first-row-level cost, pip-value, margin, and swap/rollover metrics, but it still needs broker symbol-spec ingestion, leverage/margin-call behavior, and realistic stop/TP path simulation.

4. Data bootstrap is incomplete:
   the repo can ingest replay files and now has an offline data-quality validation foundation, but it does not yet download, quarantine, and version broker-quality Forex history from scratch.

5. Risk budget is still incomplete:
   the project needs explicit risk-per-trade sizing, weekly loss caps, max open positions, leverage/free-margin/margin-level checks, symbol budgets, correlation caps, and strategy-level budgets.

6. Demo broker trading is not the same as internal paper mode:
   a separate broker-demo daemon and forward evidence flow still need to be built before any tiny-live consideration.

## Accepted Next Backlog

1. Build the first minimal end-to-end paper strategy daemon around one use case: MT5-compatible `EURUSD`, one timeframe, one signal source, no live order submission by default.
2. Add production-facing CLIs for feature building, dataset building, baseline training, walk-forward validation, and backtest execution.
3. Upgrade the FX backtest/accounting model to bid/ask and broker-symbol-aware execution before trusting M1/M5 results.
4. Extend risk config and runtime context with risk-per-trade, max open positions, weekly loss, leverage, and margin checks.
5. Completed on `2026-05-03`: add a clean release/export path that excludes virtualenvs, Git metadata, caches, generated datasets, and local evidence artifacts.

## Progress After Triage

- Completed on `2026-05-03`: clean source archive tooling in `scripts/create_release_archive.py`, with manifest dry-run/list support, exclusion coverage, and `docs/release-archive.md`.
- Completed on `2026-05-03`: offline tick/replay data-quality foundation in `src/scalper_ai/data/quality.py`, with structured reports for UTC timestamp, ordering, duplicate, bid/ask, gap, and received/event lag checks.
- Completed on `2026-05-03`: model bundle metadata contract in `src/scalper_ai/models/bundle.py`, including deterministic feature-contract hashing, UTC metadata validation, artifact references, metrics, training window provenance, and JSON save/load helpers.
- Completed on `2026-05-03`: first production CLI slice in `scripts/build_dataset.py`, `scripts/run_backtest.py`, and `scripts/run_walk_forward.py`, with shared UTC/JSON/frame helpers, unit coverage, and `docs/production-cli.md`. This covers dataset building, explicit-cost baseline backtests, and baseline walk-forward validation; feature-building and training CLIs remain separate follow-up work.
- Completed on `2026-05-03`: parallel hardening batch added `scripts/build_features.py` for offline feature frames, `scripts/run_supervised_filter.py` for leakage-safe supervised filter validation, opt-in RiskEngine budget guards for risk-per-trade/open-position/weekly-loss/margin/leverage checks, and a backwards-compatible bid/ask-aware backtest execution slice; `make PYTHON=.venv/bin/python ci` passed with full pytest `306 passed`.
- Completed on `2026-05-03`: risk-budget config/runtime wiring added config/env fields for weekly-loss, risk-per-trade, max-open-position, margin-level, and leverage budgets; runtime `RiskContext` now receives broker account state, broker-source live positions, quote-based market entry estimates, and UTC day/week realized-PnL baselines; MT5 account snapshots expose margin fields and MT5 live adapter account snapshots include gross-position effective leverage; `make PYTHON=.venv/bin/python ci` passed with full pytest `309 passed`.
- Completed on `2026-05-03`: supervised baseline filter training/export and runtime inference packaging were added with `scripts/train_supervised_filter.py`, JSON model/scaler artifacts, SHA-verified bundle metadata, `load_baseline_filter_inference_package()`, and tests for leakage-safe training cutoff behavior plus runtime scoring; `make PYTHON=.venv/bin/python ci` passed with full pytest `317 passed`.
- Completed on `2026-05-03`: first FX backtest realism slice added row-level execution cost regimes, `FxSymbolSpec`, pip-value metrics, margin-required/utilization metrics, and rollover swap-cost accounting without changing default mid-price behavior; `make PYTHON=.venv/bin/python ci` passed with full pytest `321 passed`.
- Completed on `2026-05-03`: second FX backtest realism slice added opt-in broker-style margin-call liquidation to `run_backtest()`, including same-row and next-row forced flattening, margin-level/effective-leverage/liquidation metrics, `--margin-call-level` CLI wiring, and focused tests; `make PYTHON=.venv/bin/python ci` passed with full pytest `323 passed`.

## Non-Goals

- Do not run real-money live trading from the current system.
- Do not represent MT5 smoke/probe scripts as an autonomous demo bot.
- Do not add another broker before the MT5-only path has a complete paper/demo daemon and recovery contract.
- Do not weaken paper-first defaults or live confirmation gates.

# External Audit Triage - 2026-04-30

## Source

- Report: `/Users/dzhabrailtalkanov/Downloads/deep-research-report (1).md`
- Reviewed on: `2026-04-30`
- Scope: production readiness review of the MT5/Forex scalper platform, with emphasis on live execution safety.

## Executive Verdict

The audit is directionally correct. The project has a strong research and platform foundation, but it is not ready for real-money live trading. The remaining high-risk area is not model quality; it is the execution core between signal and broker order.

The highest priority is to make RiskEngine, OMS, durable state, broker-source-of-truth reconciliation, hedging-aware MT5 behavior, deal-based accounting, and protective order handling mandatory before any live-risking order can pass.

## Findings Verified Against Current Code

### Confirmed P0 Findings

1. RiskEngine and OMS are not mandatory on the runtime submit path.
   - `src/scalper_ai/deployment/runtime.py:290` calls `require_execution_router().submit_order(...)` directly.
   - `src/scalper_ai/risk/engine.py` and `src/scalper_ai/services/oms.py` exist and are tested, but are not enforced by `DeploymentRuntime.submit_order()`.

2. MT5 live sizing uses local adapter state instead of broker state.
   - First slice completed on `2026-04-30`: `Mt5ExecutionAdapter.submit_order()` now refreshes broker positions before target-position and reduce-only sizing, `get_position()` refreshes from broker state when a quote is available, and broker position snapshots carry gross exposure and source tickets.
   - Remaining work: richer multi-ticket close workflows, broker fault-injection tests, protective order handling, symbol-specific lot constraints, and durable per-deal attribution follow-through.

3. Durable restart recovery was absent at audit time.
   - First slice completed on `2026-04-30`: `state_store.py` now provides SQLite persistence for runtime order/risk/OMS/execution/fill/position/kill-switch state, and `DeploymentRuntime.start()` reloads state before new orders.
   - Remaining work: expand broker-side recovery/fault injection for broker-only positions, partial local state, and missing/empty broker history.

4. Live PnL, fees, and fill attribution are incomplete.
   - First slice completed on `2026-04-30`: `Mt5DealState` now surfaces deal ids, order ids, side, volume, price, timestamp, commission, fee, swap, and position tickets; `Mt5OrderState` carries deal records; the live adapter creates fills from unseen deal ids and folds non-negative commission/fee/swap charges into `FillEvent.commission`.
   - Remaining work: durable/journal per-deal persistence, real-terminal non-empty history validation, and richer account-currency cost/credit semantics.

5. Production bracket / TP / SL handling is absent.
   - `src/scalper_ai/domain/trading.py:19` defines `OrderIntent` with market/limit/stop/stop_limit fields, but no bracket, parent-child, stop-loss, take-profit, or OCO semantics.
   - `src/scalper_ai/execution/mt5_client.py:491` builds MT5 requests without `sl` or `tp` fields.

6. MT5 account-mode assumptions are currently unsafe for Dukascopy hedging behavior.
   - First slice completed on `2026-04-30`: MT5 config/client/adapter now accept `account_mode='hedging'`, the MT5 overlay defaults to hedging for the observed Dukascopy demo behavior, and ticket-specific reduce-only closes pass the MT5 `position` field.
   - The real Parallels demo validation on 2026-04-29 proved Dukascopy `margin_mode=2` behaves as hedging: an opposite order opened a second position until ticket-specific flattening closed both positions.

### Confirmed P1 Findings

1. Symbol specs and quantization are not broker-symbol specific.
   - `src/scalper_ai/execution/mt5_live.py:507` uses global `base_units_per_lot`, `min_volume_lots`, and `volume_step_lots`.
   - It uses `round()`, which can round volume upward. Live risk sizing should conservatively round down by symbol-specific `volume_step`, `volume_min`, and `volume_max`.

2. Several risk config fields are not wired into enforced live risk decisions.
   - `configs/base.yaml:26` defines max spread, cooldown, volatility filter, and news filter settings.
   - `src/scalper_ai/risk/engine.py:48` `RiskLimits` currently covers position, daily loss/drawdown, order rate, stale data, and reject burst, but not max spread, post-loss cooldown, volatility guard, or news guard.
   - Runtime does not invoke RiskEngine yet, so even wired limits are not mandatory on the submit path.

3. Reconnect and MT5 supervision are partial.
   - `src/scalper_ai/execution/mt5_client.py:468` returns immediately when `_initialized` is true.
   - There is no reconnect policy, circuit breaker, terminal supervision state machine, or mandatory reconciliation after reconnect.

4. The project does not yet have a complete long-running live strategy daemon.
   - The runtime exposes startup, health, metrics, manual `submit_order()`, and `process_quote()`.
   - A production loop for market data, live features, inference, signal gating, risk, OMS, broker acknowledgements, reconciliation, journal, and monitoring still needs to be built.

5. Test coverage is strong for existing modules but missing live fault-injection scenarios.
   - Needed scenarios include restart during open order, partial fill before local save, broker position with empty local state, manual terminal position, accepted `order_check` followed by rejected `order_send`, missing history APIs, filling-mode mismatch, stale quote, duplicate intent after reconnect, and terminal disconnect during open position.

### Minor / Hygiene Finding

- `.venv`, `__pycache__`, and `.DS_Store` exist locally but are ignored by `.gitignore`. This is a ZIP/export hygiene issue, not a committed repository issue at the moment.

## Accepted Priority Backlog

### P0.A - Wire Risk + OMS Into Runtime

Status: first runtime gate slice completed on `2026-04-30`.

Goal: remove the direct `runtime -> router.submit_order()` path.

Required behavior:
- Completed: build a `RiskContext` for every `OrderIntent`.
- Completed: evaluate with `RiskEngine` before adapter/router submission.
- Completed: create and transition `OmsOrderRecord` through `NEW -> CHECKED -> SENT -> ACK/PARTIAL/FILLED/REJECTED/CANCELLED` or risk-rejected states.
- Completed: journal risk decisions, OMS transitions, broker responses, fills, and position updates in memory and through an optional writer.
- Completed: return a normalized rejected `ExecutionUpdate` when risk blocks an order, without touching the broker.
- Completed in the first P0.B slice: durable persistence and startup recovery for these runtime records.

### P0.B - Durable State Store And Startup Recovery

Status: first durable recovery slice completed on `2026-04-30`.

Goal: make live state recoverable after process crash or restart.

Required behavior:
- Completed: persist order intents, OMS transitions, broker order ids, execution updates, fills, position snapshots, risk decisions, and kill-switch state in SQLite.
- Completed: reload execution/OMS/account state during `DeploymentRuntime.start()` before accepting new orders.
- Completed: block duplicate intents after restart through recovered execution state.
- Completed: block unsafe paper fallback or unreconciled live startup when recovered live orders are still open.
- Pending: expand broker-side startup reconciliation and fault-injection tests for broker-only positions, partial local state, and missing/empty broker history.

### P0.C - Broker-Source-Of-Truth MT5 Position Handling

Status: first broker-source MT5 hedging slice completed on `2026-04-30`.

Goal: prevent local-cache sizing errors.

Required behavior:
- Completed: refresh broker positions before target-position and reduce-only sizing.
- Completed: support hedging accounts by tracking position tickets and gross exposure, not just net symbol exposure.
- Completed: pass a single unambiguous position ticket to MT5 reduce-only close requests and reject ambiguous multi-ticket closes.
- Completed: keep Dukascopy EURUSD overlay on hedging mode and IOC operational knowledge unless a fresh broker probe proves otherwise.
- Completed: reconcile broker snapshots back through existing snapshot contracts, including hidden gross hedged exposure detection.
- Pending: add richer explicit multi-ticket close orchestration and live fault-injection coverage.

### P0.D - Deal-Based Live Accounting

Status: first deal-normalization slice completed on `2026-04-30`.

Goal: make live fills and PnL auditable.

Required behavior:
- Completed: track seen deal ids.
- Completed: normalize every new broker deal separately.
- Completed: propagate commission, fee, swap, price, volume, order id, position id, and timestamps into normalized deal records and fills.
- Completed: avoid rebuilding delta fills from cumulative average price when deal history is available.
- Pending: persist deal-level attribution in durable state/journal and validate against non-empty real broker history.

### P0.E - Protective Order / Bracket Management

Goal: ensure every live position has an explicit worst-case exit path.

Required behavior:
- Extend domain contracts for protective order semantics or bracket groups.
- Support MT5 native `sl`/`tp` for the first production slice.
- Reconcile protective order presence after broker acknowledgement.
- Block or flatten when required protective orders are missing.

### P1 - Symbol Specs, Reconnect, And Risk Config Completion

Required behavior:
- Add symbol capability discovery and conservative Decimal-based quantization.
- Add MT5 reconnect policy, circuit breaker, and mandatory post-reconnect reconciliation.
- Wire max spread, cooldown, volatility, news/model/feature health guards into enforced risk decisions.
- Add fault-injection tests for the live execution failure modes listed above.

## Explicit Non-Goals For The Next Slice

- Do not put a remote LLM in the hot trading decision loop.
- Do not start with RL execution policy in production.
- Do not add another broker before MT5 live execution is recoverable and audited.
- Do not weaken paper-first live safety.
- Do not pursue dashboards or orchestration complexity before the execution core is safe.

## Next Recommended Implementation Step

Continue with P0.E plus P0.D follow-through: protective TP/SL/bracket management, durable/journal per-deal persistence, real broker history investigation, broker-side recovery fault tests, and symbol-specific quantization on top of the mandatory runtime Risk/OMS, durable recovery, broker-source MT5 hedging, and first deal-accounting gates.

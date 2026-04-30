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
   - `src/scalper_ai/execution/mt5_live.py:189` resolves current position through `self.get_position(...)`.
   - `src/scalper_ai/execution/mt5_live.py:251` returns the latest marked internal position, not a refreshed broker position.
   - Broker snapshot methods exist, but they are not used as the source of truth before target-position or reduce-only sizing.

3. Durable restart recovery is absent.
   - Current state tracking is process-local through `ExecutionStateTracker` and adapter dictionaries.
   - There is no `state_store.py`, `recovery.py`, persisted OMS ledger, or startup recovery gate that reconstructs open orders, fills, positions, and kill-switch state before running.

4. Live PnL, fees, and fill attribution are incomplete.
   - `src/scalper_ai/execution/mt5_live.py:413` builds fills with `commission=0.0` and `slippage_cost=0.0`.
   - `src/scalper_ai/execution/mt5_client.py:612` summarizes deals only into total volume and average fill price; it does not surface deal ids, commission, fee, swap, or per-deal attribution into the live adapter.

5. Production bracket / TP / SL handling is absent.
   - `src/scalper_ai/domain/trading.py:19` defines `OrderIntent` with market/limit/stop/stop_limit fields, but no bracket, parent-child, stop-loss, take-profit, or OCO semantics.
   - `src/scalper_ai/execution/mt5_client.py:491` builds MT5 requests without `sl` or `tp` fields.

6. MT5 account-mode assumptions are currently unsafe for Dukascopy hedging behavior.
   - `src/scalper_ai/execution/mt5_client.py:47` currently accepts only `account_mode='netting'`.
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

Goal: remove the direct `runtime -> router.submit_order()` path.

Required behavior:
- Build a `RiskContext` for every `OrderIntent`.
- Evaluate with `RiskEngine` before adapter/router submission.
- Create and transition `OmsOrderRecord` through `NEW -> CHECKED -> SENT -> ACK/PARTIAL/FILLED/REJECTED/CANCELLED -> RECONCILED`.
- Journal risk decisions, order requests, broker responses, fills, and position updates.
- Return a normalized rejected `ExecutionUpdate` when risk blocks an order, without touching the broker.

### P0.B - Durable State Store And Startup Recovery

Goal: make live state recoverable after process crash or restart.

Required behavior:
- Persist order intents, OMS transitions, broker order ids, fills/deals, position snapshots, risk decisions, and kill-switch state.
- On startup, reload state, fetch broker orders/positions/deals, reconcile, and only enter running state if safe.
- Enter safe/kill-switch mode when broker state and internal state cannot be reconciled.

### P0.C - Broker-Source-Of-Truth MT5 Position Handling

Goal: prevent local-cache sizing errors.

Required behavior:
- Refresh broker positions before target-position and reduce-only sizing.
- Support hedging accounts by tracking position tickets, not just net symbol exposure.
- Keep Dukascopy EURUSD on IOC unless a fresh broker probe proves otherwise.
- Reconcile broker snapshots back through existing snapshot contracts.

### P0.D - Deal-Based Live Accounting

Goal: make live fills and PnL auditable.

Required behavior:
- Track seen deal ids.
- Normalize every new broker deal separately.
- Propagate commission, fee, swap, price, volume, order id, position id, and timestamps.
- Avoid rebuilding delta fills from cumulative average price when deal history is available.

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

Start with P0.A: wire RiskEngine and OMS into `DeploymentRuntime.submit_order()` behind tests. This is the shortest path to turning existing safety modules from optional building blocks into a mandatory execution gate.

# External Audit Triage - 2026-04-30

## Source

- Report: `/Users/dzhabrailtalkanov/Downloads/deep-research-report (1).md`
- Reviewed on: `2026-04-30`
- Scope: production readiness review of the MT5/Forex scalper platform, with emphasis on live execution safety.

## Executive Verdict

The audit is directionally correct. The project has a strong research and platform foundation, but it is not ready for real-money live trading. The remaining high-risk area is not model quality; it is the execution core between signal and broker order.

The highest priority is to make RiskEngine, OMS, durable state, broker-source-of-truth reconciliation, hedging-aware MT5 behavior, deal-based accounting, and protective order handling mandatory before any live-risking order can pass.

## Findings Rechecked Against Current Code

### P0 Findings From The Report

1. RiskEngine and OMS were not mandatory on the runtime submit path at audit time.
   - First slice completed on `2026-04-30`: `DeploymentRuntime.submit_order()` now builds a `RiskContext`, evaluates `RiskEngine` before router/broker submission, records the risk decision, drives OMS transitions, and returns a normalized rejected `ExecutionUpdate` without touching the router when risk rejects an order.
   - Risk-config follow-up completed on `2026-04-30`: `RiskEngine` now has gates for max spread, post-loss cooldown, volatility guard, news guard, stale feature health, model health, and recovered durable kill switches.
   - Remaining work: connect real volatility/news/model/feature providers and symbol-specific pip/spec metadata instead of relying on the default runtime spread fallback.

2. MT5 live sizing uses local adapter state instead of broker state.
   - First slice completed on `2026-04-30`: `Mt5ExecutionAdapter.submit_order()` now refreshes broker positions before target-position and reduce-only sizing, `get_position()` refreshes from broker state when a quote is available, and broker position snapshots carry gross exposure and source tickets.
   - Remaining work: richer multi-ticket close workflows, broker fault-injection tests, protective-order repair/modify support, and fuller symbol-specific price/protection/filling constraints.

3. Durable restart recovery was absent at audit time.
   - First slice completed on `2026-04-30`: `state_store.py` now provides SQLite persistence for runtime order/risk/OMS/execution/fill/position/kill-switch state, and `DeploymentRuntime.start()` reloads state before new orders.
   - Startup recovery/fault-injection follow-up completed on `2026-04-30`: live startup now requires reconciliation after live adapter activation, blocks missing/failed startup reconciliation reports, activates a durable session kill-switch for broker/internal error drift, and tests broker-only exposure plus provider exception/empty-report faults.
   - Remaining work: real-broker non-empty history investigation, scheduled post-reconnect reconciliation, and richer operator recovery/reset workflows.

4. Live PnL, fees, and fill attribution are incomplete.
   - First slice completed on `2026-04-30`: `Mt5DealState` now surfaces deal ids, order ids, side, volume, price, timestamp, commission, fee, swap, and position tickets; `Mt5OrderState` carries deal records; the live adapter creates fills from unseen deal ids and folds non-negative commission/fee/swap charges into `FillEvent.commission`.
   - Durable/journal follow-up completed on `2026-04-30`: `FillEvent` now carries optional broker deal attribution, MT5 deal fills populate broker deal/position/cost fields, `SqliteExecutionStateStore` schema v2 persists `deal_attributions`, and FILL journal payloads preserve raw signed broker commission/fee/swap.
   - Remaining work: real-terminal non-empty history validation and richer account-currency cost/credit semantics.

5. Production bracket / TP / SL handling is absent.
   - First slice completed on `2026-04-30`: `OrderIntent` now carries optional stop-loss and take-profit prices, `Mt5TerminalClient` maps them to MT5 native `sl`/`tp`, MT5 order state carries broker-acknowledged protective prices, and reconciliation flags missing/mismatched broker protection.
   - Position fail-safe slice completed on `2026-04-30`: broker position snapshots now carry SL/TP, MT5 position normalization maps `sl`/`tp`, reconciliation flags required live MT5 positions missing broker-side protection after fill/reconnect, and runtime health activates a durable session kill-switch for that drift.
   - Bracket/OCO follow-up completed on `2026-04-30`: reconciliation derives expected position SL/TP from filled bracket intents, detects missing/mismatched/ambiguous broker-side position protection after fills or reconnects, and exposes explicit-token reduce-only flattening for drifted live positions.
   - Remaining work: richer parent-child protective-order tracking, broker-side modify/repair support where available, and real-terminal validation of broker echo/position protection behavior.

6. MT5 account-mode assumptions are currently unsafe for Dukascopy hedging behavior.
   - First slice completed on `2026-04-30`: MT5 config/client/adapter now accept `account_mode='hedging'`, the MT5 overlay defaults to hedging for the observed Dukascopy demo behavior, and ticket-specific reduce-only closes pass the MT5 `position` field.
   - The real Parallels demo validation on 2026-04-29 proved Dukascopy `margin_mode=2` behaves as hedging: an opposite order opened a second position until ticket-specific flattening closed both positions.

### Confirmed P1 Findings

1. Symbol specs and quantization are not broker-symbol specific.
   - First symbol-spec slice completed on `2026-04-30`: `Mt5TerminalClient.get_symbol_spec()` now normalizes MT5 `symbol_info`, `Mt5ExecutionAdapter` uses broker symbol specs when available, and lot volume normalization plus broker lot-to-base-unit conversion use symbol contract size with `Decimal` `ROUND_DOWN` against symbol `volume_min`, `volume_step`, and `volume_max`.
   - Symbol metadata enforcement follow-up completed on `2026-04-30`: `Mt5SymbolSpec` now includes trade/filling/execution modes, request prices are quantized by broker `point`/`digits`, stops-level distance is enforced for entry/protective prices, trade mode gates exposure-increasing orders, FOK/IOC are selected or rejected from symbol `filling_mode`, and FOK/IOC are rejected for pending orders.
   - Remaining work: apply freeze-level and order-mode metadata to broker-side modify/repair workflows and pending-order permissions, then validate against a real terminal symbol matrix.

2. Several risk config fields are not wired into enforced live risk decisions.
   - `configs/base.yaml:26` defines max spread, cooldown, volatility filter, and news filter settings.
   - First risk-config wiring slice completed on `2026-04-30`: `RiskLimits` and `RiskContext` now represent max spread, post-loss cooldown, volatility/news guards, feature/model health, and recovered kill-switch state; live runtime injects current spread into mandatory pre-trade risk decisions.
   - Remaining work: wire real signal-quality providers for volatility/news/model/feature health and replace default pip fallback with broker symbol specs.

3. Reconnect and MT5 supervision are partial.
   - `src/scalper_ai/execution/mt5_client.py:468` returns immediately when `_initialized` is true.
   - First reconnect supervision slice completed on `2026-04-30`: `Mt5TerminalClient` now probes terminal/account readiness before use, reconnects stale initialized sessions, exposes a connection snapshot, opens a circuit breaker after configured failed reconnect attempts, and wires reconnect config through typed app config, env overrides, and live factory.
   - Remaining work: long-running daemon supervision loop, operator-facing circuit reset workflow, alert routing, and mandatory reconciliation scheduling after reconnect outside health-check execution.

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
- Completed first startup recovery/fault-injection slice: live startup performs mandatory reconciliation after live adapter activation, blocks missing/failed reports, and kill-switches broker/internal error drift before new live orders.
- Pending: real-broker non-empty history investigation, scheduled post-reconnect reconciliation, and richer operator recovery/reset workflows.

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

Status: first protective-order slice completed on `2026-04-30`.

Goal: ensure every live position has an explicit worst-case exit path.

Required behavior:
- Completed: extend `OrderIntent` with optional `stop_loss_price` and `take_profit_price`.
- Completed: support MT5 native `sl`/`tp` for the first production slice.
- Completed: reconcile protective order presence after broker acknowledgement through `BrokerOrderSnapshot`.
- Completed: block exposure-increasing MT5 orders when config requires missing SL and/or TP.
- Completed: reconcile required broker-side position protection after fill/reconnect through `BrokerPositionSnapshot`.
- Completed: activate a durable runtime session kill-switch when required live MT5 position protection is missing.
- Completed first bracket/OCO lifecycle slice: reconciliation now derives expected position protection from filled internal bracket intents and flags missing, mismatched, or ambiguous broker-side SL/TP protection after fills or reconnects.
- Completed first approved flatten slice: `DeploymentRuntime.flatten_unprotected_positions()` requires the live confirmation phrase and submits reduce-only market flatten orders only for broker positions tied to position-protection reconciliation drift.
- Pending: richer parent-child protective-order state tracking, broker-side modify/repair support where supported, and operator UX around flatten approval/audit evidence.

### P1 - Symbol Specs, Reconnect, And Risk Config Completion

Required behavior:
- Add symbol capability discovery and conservative Decimal-based quantization.
- Completed first slice: broker `symbol_info` is normalized and lot sizing is conservatively quantized by symbol volume constraints.
- Add MT5 reconnect policy, circuit breaker, and mandatory post-reconnect reconciliation.
- Completed first slice: terminal/account probes, stale-session reconnect, circuit-breaker diagnostics, and config/env wiring exist in `Mt5TerminalClient`.
- Pending: daemon-level reconnect orchestration, operator reset workflow, alert routing, and explicit post-reconnect reconciliation scheduling.
- Completed first slice: wire max spread, cooldown, volatility, news/model/feature health guards, and recovered kill switches into enforced risk decisions.
- Pending: connect those guards to real providers and symbol-specific broker metadata.
- Add fault-injection tests for the live execution failure modes listed above.

## Explicit Non-Goals For The Next Slice

- Do not put a remote LLM in the hot trading decision loop.
- Do not start with RL execution policy in production.
- Do not add another broker before MT5 live execution is recoverable and audited.
- Do not weaken paper-first live safety.
- Do not pursue dashboards or orchestration complexity before the execution core is safe.

## Next Recommended Implementation Step

Continue remaining live hardening: real broker history investigation, richer protective-order repair/modify support, daemon supervision, post-reconnect reconciliation scheduling, and freeze/order-mode metadata follow-up on top of the mandatory runtime Risk/OMS, durable recovery/startup reconciliation, broker-source MT5 hedging, deal-accounting/per-deal attribution gates, protective SL/TP gates, position-protection fail-safe, symbol metadata enforcement, and approved protection-flatten workflow.

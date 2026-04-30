# Todo Next

## Active Target

- POST-PHASE — Hardening, live integration refinement, and operational stabilization

## Current Next Step

- Continue acting on the 2026-04-30 external audit triage after the first runtime Risk/OMS gate, durable recovery, broker-source MT5 hedging, deal-based accounting, protective SL/TP, risk-config guard, and MT5 symbol-spec quantization slices: next add richer bracket/position-protection workflows, deeper broker-side recovery fault tests, real-broker history/deal investigation, MT5 reconnect supervision, and fuller symbol metadata enforcement for prices/stops/filling.
- Keep the completed Parallels MT5 demo-order findings in scope while doing this work: Dukascopy `margin_mode=2` behaved as hedging, IOC is required for EURUSD, and `history_orders_get` / `history_deals_get` still returned zero rows after controlled demo fills.
- If further MT5 work is paused, validate Docker/Compose runtime packaging on a Docker-enabled host, then continue with small-batch Ruff/mypy cleanup from `docs/post-phase-roadmap.md`.

## Deliverables

- Sprint A+ operational foundation from `docs/post-phase-roadmap.md` — completed on 2026-04-27
- MT5 safe submit chain with `order_check -> order_send` guardrails — completed on 2026-04-27
- unified event journal contracts for market/signal/order/fill/risk/latency events — completed on 2026-04-27
- standalone OMS/RiskEngine state machine with kill switches and emergency flatten workflow — completed on 2026-04-27
- full Python 3.11+ environment validation, including repo-wide test execution in the actual target interpreter — completed on 2026-04-27 with Python 3.12.13
- execution-aware simulator V2 with latency, partial-fill, cancel/replace, stale/closed-market, queue-position, and execution-quality metrics — completed on 2026-04-27
- baseline strategy suite with spread/mean-reversion, OFI/imbalance, volatility breakout, sensitivity scenarios, and walk-forward reports — completed on 2026-04-28
- unified validation gate report for backtest, walk-forward, execution stress, latency/slippage, risk flags, and regime breakdown — completed on 2026-04-28
- paper/shadow champion-challenger decision reporting — completed on 2026-04-28
- interpretable supervised baseline filter with leakage-safe walk-forward evaluation — completed on 2026-04-28
- observability alert-rule docs and platform roadmap — completed on 2026-04-28
- production checklist, incident/postmortem templates, and release/incident runbooks — completed on 2026-04-28
- Windows MT5 terminal connection and broker-side order_check smoke against an authorized demo session — completed on 2026-04-28 without order_send
- safe MT5 broker probe through the normalized client, including terminal/account/symbol/tick/history diagnostics and broker-side FOK order_check — completed on 2026-04-28 without order_send
- MT5 Python bridge order-comment limit discovery and client-side sanitize/clamp fix — completed on 2026-04-28
- paper-safe Docker/Compose runtime packaging around the PHASE 12 runtime — completed on 2026-04-28 as source/config; Docker build/run validation remains pending on a Docker-enabled host
- full-repo Ruff/mypy cleanup baseline — completed on 2026-04-28 as `docs/lint-typecheck-baseline.md`; cleanup remains pending
- local JSONL alert transport for health snapshots — completed on 2026-04-28; network alert transport remains pending after runtime topology validation
- first scripts Ruff cleanup batch — completed on 2026-04-28; full Ruff backlog reduced from `511` to `496`
- config-layer Ruff cleanup batch — completed on 2026-04-28; full Ruff backlog reduced from `496` to `434`
- HTTP webhook alert transport — completed on 2026-04-28 with config/env wiring; concrete endpoint routing remains pending until deployment topology is chosen
- logging-utils Ruff cleanup batch — completed on 2026-04-28; full Ruff backlog reduced from `434` to `433`
- journal Ruff cleanup batch — completed on 2026-04-28; full Ruff backlog reduced from `433` to `416`
- OMS Ruff cleanup batch — completed on 2026-04-28; full Ruff backlog reduced from `416` to `409`
- validation Ruff cleanup batch — completed on 2026-04-28; full Ruff backlog reduced from `409` to `402`
- models Ruff cleanup batch — completed on 2026-04-28; full Ruff backlog reduced from `402` to `392`
- risk Ruff cleanup batch — completed on 2026-04-28; full Ruff backlog reduced from `392` to `374`
- Parallels Windows 11 MT5 environment and Dukascopy demo validation — completed on 2026-04-28 without SSH and without `order_send`: `mt5_smoke.py` connected to account `610769553`; EURUSD IOC `order_check` passed with `retcode=0`; EURUSD FOK was rejected as unsupported filling mode
- Parallels deeper MT5 history/permission check — completed on 2026-04-28 without `order_send`: one-year raw history is empty, account trading permissions are enabled, terminal-side trading permission is disabled
- Docker/runtime availability check — completed on 2026-04-28: Docker is unavailable on local macOS Codex and Parallels Windows; Compose YAML parse plus local paper runtime describe/health/metrics passed
- Controlled Parallels MT5 demo-order validation — completed on 2026-04-29 after explicit operator approval: minimum-volume EURUSD IOC order filled, initial auto-flatten exposed hedging behavior, position-ticket flatten closed all remaining EURUSD positions, final smoke showed zero open orders/positions, and raw history APIs still returned zero orders/deals
- External live-readiness audit triage — completed on 2026-04-30 as `docs/external-audit-2026-04-30.md`; confirmed P0 gaps are mandatory Risk/OMS runtime wiring, durable state/recovery, broker-source-of-truth MT5 sizing, hedging-aware execution/reconciliation, deal-based accounting, and protective order management
- P0.A first runtime gate slice — completed on 2026-04-30: `DeploymentRuntime.submit_order()` now enforces RiskEngine before router/broker submit, drives OMS checked/sent/final/process-update or risk-rejected states, and records risk/OMS/execution journal events; targeted Ruff passed and full `.venv/bin/pytest` passed with `173 passed`
- P0.B first durable recovery slice — completed on 2026-04-30: `SqliteExecutionStateStore` persists runtime order/risk/OMS/execution/fill/position/kill-switch state, `DeploymentRuntime.start()` reloads state before new orders, duplicate intents after restart are blocked by recovered state, open live recovered orders block unsafe paper fallback or unreconciled live startup, and full `.venv/bin/pytest` passed with `176 passed`
- P0.C first broker-source MT5 hedging slice — completed on 2026-04-30: MT5 sizing refreshes broker positions before target/reduce-only decisions, hedging mode tracks tickets and gross exposure, single-ticket reduce-only closes pass MT5 `position`, ambiguous multi-ticket closes are rejected, reconciliation flags hidden hedged gross exposure, the MT5 overlay defaults to hedging for Dukascopy demo behavior, targeted Ruff passed, targeted pytest passed with `33 passed`, and full `.venv/bin/pytest` passed with `182 passed`
- P0.D first deal-based live accounting slice — completed on 2026-04-30: MT5 deals are normalized as `Mt5DealState`, `Mt5OrderState` carries deal records, the MT5 live adapter builds fills from unseen broker deal ids with commission/fee/swap costs before falling back to cumulative deltas, script JSON serialization includes normalized deal records, targeted Ruff passed, targeted pytest passed with `22 passed`, and full `.venv/bin/pytest` passed with `183 passed`
- P0.E first protective-order slice — completed on 2026-04-30: `OrderIntent` carries optional `stop_loss_price`/`take_profit_price`, MT5 requests/states map them to native `sl`/`tp`, MT5 config can require SL and/or TP for exposure-increasing orders, reconciliation flags missing/mismatched broker protective prices after acknowledgement, `mt5_smoke.py` serializes protective fields, targeted Ruff passed, targeted pytest passed with `43 passed`, compileall passed, and full `.venv/bin/pytest` passed with `188 passed`
- P1 risk-config guard slice — completed on 2026-04-30: `RiskEngine` now enforces max spread, post-loss cooldown, volatility/news guards, feature/model health, and recovered durable kill-switch state before router/broker submission; live runtime injects spread into risk context; targeted Ruff passed and targeted pytest passed with `40 passed`
- P1 MT5 symbol-spec quantization slice — completed on 2026-04-30: `Mt5TerminalClient.get_symbol_spec()` normalizes broker `symbol_info`, `Mt5ExecutionAdapter` uses symbol specs when available, lot sizing and broker lot-to-base-unit normalization now use symbol contract size plus `Decimal` `ROUND_DOWN` by broker `volume_min`/`volume_step`/`volume_max`, oversize/undersize quantities return structured rejections, targeted Ruff passed, targeted pytest passed with `23 passed`, combined safety/MT5 pytest passed with `61 passed`, compileall passed, and full `.venv/bin/pytest` passed with `199 passed`
- real-terminal validation and refinement of the new MT5-backed client, reusing the existing reconciliation and connectivity contracts plus the new preflight/auto-discovery layer
- deeper operational hardening for Docker/Compose runtime validation, network alert transport wiring, and dependency supervision beyond the current broker health checks
- production-readiness cleanup to retire the full-repo lint/typecheck baseline and harden startup ergonomics

## Must-Have Capabilities

- keep paper mode as the default runtime posture unless explicit live-safe conditions are met
- preserve clear boundaries between research, paper, and live execution concerns
- validate the full stack in the actual target runtime, not only the reduced local thread environment
- keep replay/mock fallback available when broker or infrastructure dependencies are unavailable

## Implementation Guidance

- use `docs/post-phase-roadmap.md` as the canonical post-phase backlog
- continue with MT5 terminal validation when the Windows terminal is available; if it is paused, continue with platform/runtime hardening that does not require a broker
- keep deployment and hardening work separate from domain, model, and execution math
- reuse the PHASE 12 runtime, health, and metrics surfaces instead of duplicating startup logic
- favor explicit safety checks, reconciliation, and observability over implicit automation
- use the new paper-safe runtime as the default fallback for environments missing live dependencies

## Validation Goal

- keep repository-wide `pytest` green in Python 3.12.13 and future Python 3.11+ CI/target environments
- add targeted validation around the MT5-backed live adapter, real terminal behavior, non-empty history/deal normalization, reconciliation, and long-running runtime behavior
- keep deployment CLI and paper-safe runtime wiring working while hardening the live path
- keep validation gates and shadow reports as required promotion artifacts for new strategies

## After This Roadmap

- evaluate whether a new formal phase breakdown is needed for live rollout and operations maturity

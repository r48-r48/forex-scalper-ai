# Todo Next

## Active Target

- POST-PHASE — Hardening, live integration refinement, and operational stabilization

## Current Next Step

- Continue MT5 demo validation beyond the completed Windows terminal connection/order_check/broker-probe smoke: explicit terminal path, optional env credentials, non-empty history/deal normalization, and controlled demo-order behavior only after explicit operator approval.
- If further MT5 work is paused, validate Docker/Compose runtime packaging on a Docker-enabled host, then continue with network alert transport wiring and small-batch Ruff/mypy cleanup from `docs/post-phase-roadmap.md`.

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

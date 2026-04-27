# Todo Next

## Active Target

- POST-PHASE — Hardening, live integration refinement, and operational stabilization

## Current Next Step

- Real MT5 terminal validation when package, terminal, credentials, and live confirmation are available; otherwise P1.1 — Execution-Aware Simulator V2 from `docs/post-phase-roadmap.md`

## Deliverables

- Sprint A+ operational foundation from `docs/post-phase-roadmap.md` — completed on 2026-04-27
- MT5 safe submit chain with `order_check -> order_send` guardrails — completed on 2026-04-27
- unified event journal contracts for market/signal/order/fill/risk/latency events — completed on 2026-04-27
- standalone OMS/RiskEngine state machine with kill switches and emergency flatten workflow — completed on 2026-04-27
- full Python 3.11+ environment validation, including repo-wide test execution in the actual target interpreter — completed on 2026-04-27 with Python 3.12.13
- real-terminal validation and refinement of the new MT5-backed client, reusing the existing reconciliation and connectivity contracts plus the new preflight/auto-discovery layer
- deeper operational hardening for long-running services, alerting, and dependency supervision beyond the current broker health checks
- production-readiness cleanup around packaging and startup ergonomics

## Must-Have Capabilities

- keep paper mode as the default runtime posture unless explicit live-safe conditions are met
- preserve clear boundaries between research, paper, and live execution concerns
- validate the full stack in the actual target runtime, not only the reduced local thread environment
- keep replay/mock fallback available when broker or infrastructure dependencies are unavailable

## Implementation Guidance

- use `docs/post-phase-roadmap.md` as the canonical post-phase backlog
- continue with real MT5 terminal validation when dependencies are available; if they remain unavailable, continue with P1.1 execution-aware simulator work
- keep deployment and hardening work separate from domain, model, and execution math
- reuse the PHASE 12 runtime, health, and metrics surfaces instead of duplicating startup logic
- favor explicit safety checks, reconciliation, and observability over implicit automation
- use the new paper-safe runtime as the default fallback for environments missing live dependencies

## Validation Goal

- keep repository-wide `pytest` green in Python 3.12.13 and future Python 3.11+ CI/target environments
- add targeted validation around the MT5-backed live adapter, real terminal behavior, preflight diagnostics, reconciliation, and long-running runtime behavior
- keep deployment CLI and paper-safe runtime wiring working while hardening the live path

## After This Roadmap

- evaluate whether a new formal phase breakdown is needed for live rollout and operations maturity

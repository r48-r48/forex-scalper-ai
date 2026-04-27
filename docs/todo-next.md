# Todo Next

## Active Target

- POST-PHASE — Hardening, live integration refinement, and operational stabilization

## Current Next Step

- P0.4 — OMS/RiskEngine State Machine from `docs/post-phase-roadmap.md`

## Deliverables

- Sprint A+ operational foundation from `docs/post-phase-roadmap.md` — completed on 2026-04-27
- MT5 safe submit chain with `order_check -> order_send` guardrails — completed on 2026-04-27
- unified event journal contracts for market/signal/order/fill/risk/latency events — completed on 2026-04-27
- full Python 3.11+ environment validation, including repo-wide test execution in the actual target interpreter
- real-terminal validation and refinement of the new MT5-backed client, reusing the existing reconciliation and connectivity contracts plus the new preflight/auto-discovery layer
- standalone OMS/RiskEngine state machine with kill switches and emergency flatten workflow
- deeper operational hardening for long-running services, alerting, and dependency supervision beyond the current broker health checks
- production-readiness cleanup around packaging and startup ergonomics

## Must-Have Capabilities

- keep paper mode as the default runtime posture unless explicit live-safe conditions are met
- preserve clear boundaries between research, paper, and live execution concerns
- validate the full stack in the actual target runtime, not only the reduced local thread environment
- keep replay/mock fallback available when broker or infrastructure dependencies are unavailable

## Implementation Guidance

- use `docs/post-phase-roadmap.md` as the canonical post-phase backlog
- continue with P0.4 unless a blocking live-integration bug appears
- keep deployment and hardening work separate from domain, model, and execution math
- reuse the PHASE 12 runtime, health, and metrics surfaces instead of duplicating startup logic
- favor explicit safety checks, reconciliation, and observability over implicit automation
- use the new paper-safe runtime as the default fallback for environments missing live dependencies

## Validation Goal

- run repository-wide `pytest` in Python 3.11+ with the full dependency set installed
- add targeted validation around the MT5-backed live adapter, real terminal behavior, preflight diagnostics, reconciliation, and long-running runtime behavior
- keep deployment CLI and paper-safe runtime wiring working while hardening the live path

## After This Roadmap

- evaluate whether a new formal phase breakdown is needed for live rollout and operations maturity
